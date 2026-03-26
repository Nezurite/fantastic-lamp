from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation, getcontext
from typing import List
import json
import os
import random
import sys
import time

from colorama import Fore, Back, Style, init


getcontext().prec = 2000

RISK_SCHEDULE = [5, 10, 15, 20, 25, 35, 50]
START_AMOUNT_CENTS = 1
SAVE_FILE = "lucky_doors_save.json"
LUCKY_EVENT_CHANCE = 0.12

DIFFICULTIES = {
    "casual": {"risk_shift": -5, "reward_factor": Decimal("2.20"), "label": "Casual"},
    "standard": {"risk_shift": 0, "reward_factor": Decimal("2.00"), "label": "Standard"},
    "hardcore": {"risk_shift": 8, "reward_factor": Decimal("2.60"), "label": "Hardcore"},
}


class Color:
    RESET = Style.RESET_ALL
    BOLD = Style.BRIGHT
    DIM = Style.DIM

    RED = Fore.RED
    GREEN = Fore.GREEN
    YELLOW = Fore.YELLOW
    BLUE = Fore.BLUE
    MAGENTA = Fore.MAGENTA
    CYAN = Fore.CYAN
    WHITE = Fore.WHITE

    BG_RED = Back.RED
    BG_GREEN = Back.GREEN
    BG_BLUE = Back.BLUE


@dataclass
class HistoryEntry:
    door: int
    risk: int
    outcome: str
    before_cents: int
    after_cents: int


@dataclass
class Milestone:
    title: str
    description: str


@dataclass
class LuckyEvent:
    title: str
    description: str
    bonus_cents: int


class LuckyDoorsCLI:
    def __init__(self) -> None:
        init(autoreset=True)
        self.save_data = self.load_save_data()
        self.running = True
        self.reset_run()

    # ------------------------------------------------------------------
    # System helpers
    # ------------------------------------------------------------------
    def clear_screen(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")

    def wait(self, seconds: float) -> None:
        if self.get_setting("animations", True):
            time.sleep(seconds)

    def pause(self, message: str = "Press Enter to continue...") -> None:
        input(self.color_text(message, Color.DIM))

    def color_text(self, text: str, *styles: str) -> str:
        return "".join(styles) + text + Color.RESET

    def divider(self, char: str = "=") -> str:
        return self.color_text(char * 82, Color.DIM)

    # ------------------------------------------------------------------
    # Save data and upgrades
    # ------------------------------------------------------------------
    def default_save_data(self) -> dict:
        return {
            "bank_cents": 0,
            "upgrades": {
                "start_amount": 0,
                "risk_shield": 0,
                "cash_bonus": 0,
                "revive_token": 0,
            },
            "settings": {
                "animations": True,
            },
            "stats": {
                "runs_played": 0,
                "best_run_cents": START_AMOUNT_CENTS,
                "best_door": 1,
                "total_cashed_out_cents": 0,
                "classic_wins": 0,
                "classic_losses": 0,
                "classic_cashouts": 0,
                "best_win_streak": 0,
                "bet_wins": 0,
                "bet_losses": 0,
                "total_bet_staked_cents": 0,
                "total_bet_profit_cents": 0,
            },
        }

    def load_save_data(self) -> dict:
        if not os.path.exists(SAVE_FILE):
            data = self.default_save_data()
            self.write_save_data(data)
            return data

        try:
            with open(SAVE_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (json.JSONDecodeError, OSError):
            data = self.default_save_data()
            self.write_save_data(data)
            return data

        default = self.default_save_data()
        for key, value in default.items():
            if key not in data:
                data[key] = value
            elif isinstance(value, dict):
                for subkey, subvalue in value.items():
                    data[key].setdefault(subkey, subvalue)

        self.write_save_data(data)
        return data

    def write_save_data(self, data: dict) -> None:
        with open(SAVE_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

    def save_game(self) -> None:
        self.write_save_data(self.save_data)

    def get_upgrade_level(self, name: str) -> int:
        return int(self.save_data["upgrades"].get(name, 0))

    def get_start_amount_upgrade_cost(self) -> int:
        level = self.get_upgrade_level("start_amount")
        return 25 * (4 ** level)

    def get_risk_shield_upgrade_cost(self) -> int:
        level = self.get_upgrade_level("risk_shield")
        return 50 * (4 ** level)

    def get_cash_bonus_upgrade_cost(self) -> int:
        level = self.get_upgrade_level("cash_bonus")
        return 75 * (4 ** level)

    def get_revive_upgrade_cost(self) -> int:
        level = self.get_upgrade_level("revive_token")
        return 250 * (3 ** level)

    def get_start_amount_cents(self) -> int:
        level = self.get_upgrade_level("start_amount")
        return START_AMOUNT_CENTS * (2 ** level)

    def get_cash_bonus_percent(self) -> int:
        level = self.get_upgrade_level("cash_bonus")
        return level * 10

    def get_setting(self, key: str, default: bool = True) -> bool:
        return bool(self.save_data.get("settings", {}).get(key, default))

    # ------------------------------------------------------------------
    # Run state
    # ------------------------------------------------------------------
    def reset_run(self) -> None:
        self.door = 1
        self.difficulty_key = "standard"
        self.current_cents = self.get_start_amount_cents()
        self.peak_cents = self.current_cents
        self.current_streak = 0
        self.revive_used = False
        self.history: List[HistoryEntry] = []
        self.milestones: List[Milestone] = []
        self.run_events: List[LuckyEvent] = []
        self.unlocked_milestones: set[str] = set()
        self.highest_round_threshold_cents = 0
        self.fifty_unlocked = False
        self.in_run = False

    def get_base_risk_percent(self, door: int) -> int:
        index = min(door - 1, len(RISK_SCHEDULE) - 1)
        return RISK_SCHEDULE[index]

    def get_effective_risk_percent(self, door: int) -> int:
        base_risk = self.get_base_risk_percent(door)
        shield = self.get_upgrade_level("risk_shield")
        difficulty_shift = int(DIFFICULTIES[self.difficulty_key]["risk_shift"])
        return min(90, max(1, base_risk - shield + difficulty_shift))

    def next_reward_cents(self) -> int:
        reward = (Decimal(self.current_cents) * DIFFICULTIES[self.difficulty_key]["reward_factor"]).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return int(reward)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------
    def format_money(self, cents: int) -> str:
        sign = "-" if cents < 0 else ""
        cents = abs(cents)

        if cents < 10**15:
            dollars = Decimal(cents) / Decimal(100)
            return f"{sign}${dollars:,.2f}"

        digits = str(cents)
        whole_digits = max(1, len(digits) - 2)
        exponent = whole_digits - 1
        first = digits[0]
        second = digits[1] if len(digits) > 1 else "0"
        third = digits[2] if len(digits) > 2 else "0"
        return f"{sign}${first}.{second}{third}e+{exponent}"

    def format_threshold_label(self, cents: int) -> str:
        if cents < 100:
            return self.format_money(cents)
        dollars = cents // 100
        return f"${dollars:,}"

    def format_decimal(self, value: Decimal) -> str:
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text

    def format_multiplier(self, multiplier: Decimal) -> str:
        return f"x{self.format_decimal(multiplier)}"

    def format_percent_decimal(self, percent: Decimal) -> str:
        return f"{self.format_decimal(percent)}%"

    def risk_flavor(self, risk: int) -> str:
        if risk <= 5:
            return "Low danger. The run is still calm."
        if risk <= 15:
            return "Tempting odds. Greed is waking up."
        if risk <= 25:
            return "The tension is real now. One miss and it is over."
        if risk < 50:
            return "Danger zone. One bad door kills the run."
        return "Pure coin-flip territory. Glory or ruin."

    def format_upgrade_summary(self) -> List[str]:
        return [
            f"Start Amount Lv.{self.get_upgrade_level('start_amount')} -> start run with {self.format_money(self.get_start_amount_cents())}",
            f"Risk Shield  Lv.{self.get_upgrade_level('risk_shield')} -> -{self.get_upgrade_level('risk_shield')}% permanent risk",
            f"Cash Bonus   Lv.{self.get_upgrade_level('cash_bonus')} -> +{self.get_cash_bonus_percent()}% bank bonus on cash out",
            f"Revive Token Lv.{self.get_upgrade_level('revive_token')} -> {self.get_upgrade_level('revive_token')} one-time save(s) per run",
        ]

    def render_risk_bar(self, risk: int) -> str:
        filled = min(20, max(1, round((risk / 100) * 20)))
        return f"[{'#' * filled}{'.' * (20 - filled)}] {risk:>2}%"

    def render_doors(self) -> None:
        print(self.color_text("┌────────┐  ┌────────┐  ┌────────┐", Color.DIM))
        print(self.color_text("│  DOOR  │  │  DOOR  │  │  DOOR  │", Color.DIM))
        print(self.color_text("│   1    │  │   2    │  │   3    │", Color.DIM))
        print(self.color_text("└────────┘  └────────┘  └────────┘", Color.DIM))

    # ------------------------------------------------------------------
    # Bet mode helpers
    # ------------------------------------------------------------------
    def multiplier_to_loss_probability(self, multiplier: Decimal) -> Decimal:
        return Decimal(1) - (Decimal(1) / multiplier)

    def calculate_payout_cents(self, stake_cents: int, multiplier: Decimal) -> int:
        stake_dollars = Decimal(stake_cents) / Decimal(100)
        payout_dollars = (stake_dollars * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        payout_cents = int((payout_dollars * 100).to_integral_value(rounding=ROUND_HALF_UP))
        return payout_cents

    def parse_money_input(self, text: str) -> int | None:
        cleaned = text.strip().replace("$", "").replace(",", "")
        if not cleaned:
            return None

        try:
            value = Decimal(cleaned)
        except InvalidOperation:
            return None

        if value <= 0:
            return None

        value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))

    def parse_multiplier_input(self, text: str) -> Decimal | None:
        cleaned = text.strip().lower().replace("x", "")
        if not cleaned:
            return None

        try:
            value = Decimal(cleaned)
        except InvalidOperation:
            return None

        if value <= Decimal("1"):
            return None

        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ------------------------------------------------------------------
    # History and milestones
    # ------------------------------------------------------------------
    def add_history_entry(self, outcome: str, before_cents: int, after_cents: int) -> None:
        entry = HistoryEntry(
            door=self.door,
            risk=self.get_effective_risk_percent(self.door),
            outcome=outcome,
            before_cents=before_cents,
            after_cents=after_cents,
        )
        self.history.append(entry)

    def add_milestone(self, milestone_id: str, title: str, description: str) -> None:
        if milestone_id in self.unlocked_milestones:
            return

        self.unlocked_milestones.add(milestone_id)
        self.milestones.append(Milestone(title=title, description=description))

        print()
        print(self.divider("="))
        print(self.color_text("MILESTONE UNLOCKED", Color.BOLD, Color.YELLOW))
        print(self.color_text(title, Color.BOLD, Color.WHITE))
        print(self.color_text(description, Color.CYAN))
        print(self.divider("="))
        self.pause()

    def check_milestones(self) -> None:
        risk = self.get_effective_risk_percent(self.door)

        if not self.fifty_unlocked and risk >= 50:
            self.fifty_unlocked = True
            self.add_milestone(
                "fifty-percent",
                "50% risk reached",
                "From here on, every risky door is a true coin flip.",
            )

        threshold = 1000  # $10.00 in cents
        while threshold <= self.current_cents:
            threshold *= 10
        threshold //= 10

        if threshold >= 1000 and threshold > self.highest_round_threshold_cents:
            self.highest_round_threshold_cents = threshold
            label = self.format_threshold_label(threshold)
            self.add_milestone(
                f"round-{threshold}",
                f"Crossed {label}",
                f"Your current stack just pushed past {label}.",
            )

    def maybe_trigger_lucky_event(self) -> None:
        if random.random() > LUCKY_EVENT_CHANCE:
            return

        bonus = max(1, self.current_cents // 5)
        self.current_cents += bonus
        self.peak_cents = max(self.peak_cents, self.current_cents)
        event = LuckyEvent(
            title="Lucky Echo",
            description="A hidden vault clicked open and boosted your stack.",
            bonus_cents=bonus,
        )
        self.run_events.append(event)

        print(self.color_text("\nLUCKY EVENT!", Color.BOLD, Color.MAGENTA))
        print(self.color_text(event.title, Color.BOLD, Color.WHITE))
        print(self.color_text(event.description, Color.CYAN))
        print(self.color_text(f"Bonus gained: +{self.format_money(bonus)}", Color.GREEN))

    # ------------------------------------------------------------------
    # Screens
    # ------------------------------------------------------------------
    def print_title_screen(self) -> None:
        self.clear_screen()
        print(self.divider("#"))
        print(self.color_text("LUCKY DOORS - CLI EDITION", Color.BOLD, Color.YELLOW))
        print(self.color_text("Classic mode + Bet mode + difficulty, streaks, and revives.", Color.WHITE))
        print(self.divider("#"))
        print()
        print(self.color_text(f"Persistent bank: {self.format_money(self.save_data['bank_cents'])}", Color.BOLD, Color.GREEN))
        print(self.color_text("Classic mode uses upgrades. Bet mode ignores all upgrades.", Color.CYAN))
        print()

        for line in self.format_upgrade_summary():
            print(self.color_text(f"- {line}", Color.WHITE))

        print()
        print(self.divider("-"))
        print(self.color_text("[P] Play classic run", Color.GREEN))
        print(self.color_text("[B] Bet mode", Color.MAGENTA))
        print(self.color_text("[S] Shop / upgrades", Color.YELLOW))
        print(self.color_text("[T] Lifetime stats", Color.CYAN))
        print(self.color_text("[O] Options", Color.WHITE))
        print(self.color_text("[Q] Quit", Color.RED))
        print(self.divider("-"))

    def print_status(self) -> None:
        risk = self.get_effective_risk_percent(self.door)
        base_risk = self.get_base_risk_percent(self.door)
        bonus_percent = self.get_cash_bonus_percent()

        self.clear_screen()
        print(self.divider("#"))
        print(self.color_text(f"CLASSIC MODE - DOOR #{self.door}", Color.BOLD, Color.YELLOW))
        print(self.divider("#"))
        print(self.color_text(f"Current amount      : {self.format_money(self.current_cents)}", Color.BOLD, Color.GREEN))
        print(self.color_text(f"Next reward         : {self.format_money(self.next_reward_cents())}", Color.WHITE))
        print(self.color_text(f"Current risk        : {risk}%", Color.RED if risk >= 25 else Color.YELLOW))
        print(self.color_text(f"Risk meter          : {self.render_risk_bar(risk)}", Color.RED if risk >= 25 else Color.YELLOW))
        print(self.color_text(f"Base risk           : {base_risk}%", Color.DIM))
        print(self.color_text(f"Difficulty          : {DIFFICULTIES[self.difficulty_key]['label']}", Color.CYAN))
        print(self.color_text(f"Win streak          : {self.current_streak}", Color.YELLOW))
        print(self.color_text(f"Bank                : {self.format_money(self.save_data['bank_cents'])}", Color.CYAN))
        print(self.color_text(f"Cash-out bonus      : +{bonus_percent}% + streak bonus", Color.MAGENTA))
        print(self.color_text(f"Revive tokens       : {self.get_upgrade_level('revive_token')}", Color.WHITE))
        print(self.color_text(f"Peak this run       : {self.format_money(self.peak_cents)}", Color.WHITE))
        print(self.color_text(f"Mood                : {self.risk_flavor(risk)}", Color.DIM))
        self.render_doors()
        print(self.divider("-"))

    def print_recent_history(self, max_entries: int = 8) -> None:
        print(self.color_text("Recent history", Color.BOLD, Color.CYAN))

        if not self.history:
            print(self.color_text("  No entries yet.", Color.DIM))
            print()
            return

        for entry in self.history[-max_entries:]:
            before = self.format_money(entry.before_cents)
            after = self.format_money(entry.after_cents)

            if entry.outcome == "win":
                label = self.color_text("WIN ", Color.BOLD, Color.GREEN)
            elif entry.outcome == "lose":
                label = self.color_text("LOSE", Color.BOLD, Color.RED)
            else:
                label = self.color_text("CASH", Color.BOLD, Color.YELLOW)

            print(
                f"  Door #{entry.door:<4} | Risk {entry.risk:>2}% | "
                f"{before:<16} -> {after:<16} | {label}"
            )

        print()

    def print_recent_milestones(self) -> None:
        print(self.color_text("Milestones", Color.BOLD, Color.MAGENTA))

        if not self.milestones:
            print(self.color_text("  None unlocked yet.", Color.DIM))
            print()
            return

        for milestone in self.milestones[-5:]:
            print(self.color_text(f"  - {milestone.title}", Color.YELLOW))

        print()

    def print_recent_events(self) -> None:
        print(self.color_text("Lucky events", Color.BOLD, Color.BLUE))
        if not self.run_events:
            print(self.color_text("  No lucky events this run.", Color.DIM))
            print()
            return

        for event in self.run_events[-3:]:
            print(self.color_text(f"  - {event.title}: +{self.format_money(event.bonus_cents)}", Color.GREEN))
        print()

    def print_run_menu(self) -> None:
        print(self.divider("-"))
        print(self.color_text("[R] Risk it  -> scaled reward or lose all", Color.RED))
        print(self.color_text("[C] Cash out -> secure current amount into your bank", Color.GREEN))
        print(self.color_text("[H] Full history", Color.CYAN))
        print(self.color_text("[M] Full milestones", Color.MAGENTA))
        print(self.color_text("[X] End run and return to main menu", Color.YELLOW))
        print(self.divider("-"))

    def print_shop(self) -> None:
        self.clear_screen()
        print(self.divider("#"))
        print(self.color_text("UPGRADE SHOP", Color.BOLD, Color.YELLOW))
        print(self.divider("#"))
        print(self.color_text(f"Available bank: {self.format_money(self.save_data['bank_cents'])}", Color.BOLD, Color.GREEN))
        print(self.color_text("These upgrades only affect Classic mode.", Color.CYAN))
        print()

        print(self.color_text("[1] Start Amount", Color.CYAN))
        print(f"    Level : {self.get_upgrade_level('start_amount')}")
        print(f"    Effect: Start each run with {self.format_money(self.get_start_amount_cents())}")
        print(f"    Next  : {self.format_money(self.get_start_amount_cents() * 2)}")
        print(f"    Cost  : {self.format_money(self.get_start_amount_upgrade_cost())}")
        print()

        print(self.color_text("[2] Risk Shield", Color.CYAN))
        print(f"    Level : {self.get_upgrade_level('risk_shield')}")
        print(f"    Effect: Permanently reduce all Classic mode risks by {self.get_upgrade_level('risk_shield')}%")
        print(f"    Next  : {self.get_upgrade_level('risk_shield') + 1}% reduction")
        print(f"    Cost  : {self.format_money(self.get_risk_shield_upgrade_cost())}")
        print()

        print(self.color_text("[3] Cash Bonus", Color.CYAN))
        print(f"    Level : {self.get_upgrade_level('cash_bonus')}")
        print(f"    Effect: +{self.get_cash_bonus_percent()}% bonus bank money on every Classic mode cash out")
        print(f"    Next  : +{self.get_cash_bonus_percent() + 10}% bonus")
        print(f"    Cost  : {self.format_money(self.get_cash_bonus_upgrade_cost())}")
        print()

        print(self.color_text("[4] Revive Token", Color.CYAN))
        print(f"    Level : {self.get_upgrade_level('revive_token')}")
        print("    Effect: Each level grants one one-time automatic second chance per run")
        print(f"    Cost  : {self.format_money(self.get_revive_upgrade_cost())}")
        print()

        print(self.divider("-"))
        print(self.color_text("[B] Back", Color.YELLOW))
        print(self.divider("-"))

    def print_lifetime_stats(self) -> None:
        stats = self.save_data["stats"]
        self.clear_screen()
        print(self.divider("#"))
        print(self.color_text("LIFETIME STATS", Color.BOLD, Color.YELLOW))
        print(self.divider("#"))
        print(self.color_text(f"Runs played            : {stats['runs_played']}", Color.WHITE))
        print(self.color_text(f"Best amount reached    : {self.format_money(stats['best_run_cents'])}", Color.GREEN))
        print(self.color_text(f"Deepest door reached   : #{stats['best_door']}", Color.CYAN))
        print(self.color_text(f"Total cashed out ever  : {self.format_money(stats['total_cashed_out_cents'])}", Color.MAGENTA))
        print(self.color_text(f"Classic wins           : {stats['classic_wins']}", Color.GREEN))
        print(self.color_text(f"Classic losses         : {stats['classic_losses']}", Color.RED))
        print(self.color_text(f"Classic cashouts       : {stats['classic_cashouts']}", Color.YELLOW))
        print(self.color_text(f"Best win streak        : {stats['best_win_streak']}", Color.CYAN))
        print(self.color_text(f"Bet wins               : {stats['bet_wins']}", Color.GREEN))
        print(self.color_text(f"Bet losses             : {stats['bet_losses']}", Color.RED))
        print(self.color_text(f"Total bet staked       : {self.format_money(stats['total_bet_staked_cents'])}", Color.WHITE))
        print(self.color_text(f"Net bet profit         : {self.format_money(stats['total_bet_profit_cents'])}", Color.MAGENTA))
        print(self.color_text(f"Current bank           : {self.format_money(self.save_data['bank_cents'])}", Color.BOLD, Color.GREEN))
        print()

        for line in self.format_upgrade_summary():
            print(self.color_text(f"- {line}", Color.WHITE))

        print()
        self.pause()

    def print_full_history(self) -> None:
        self.clear_screen()
        print(self.divider("#"))
        print(self.color_text("FULL RUN HISTORY", Color.BOLD, Color.YELLOW))
        print(self.divider("#"))

        if not self.history:
            print(self.color_text("No history yet.", Color.DIM))
            print()
            self.pause()
            return

        for entry in self.history:
            before = self.format_money(entry.before_cents)
            after = self.format_money(entry.after_cents)

            if entry.outcome == "win":
                outcome = self.color_text("WIN", Color.GREEN, Color.BOLD)
            elif entry.outcome == "lose":
                outcome = self.color_text("LOSE", Color.RED, Color.BOLD)
            else:
                outcome = self.color_text("CASH", Color.YELLOW, Color.BOLD)

            print(
                f"Door #{entry.door:<4} | Risk {entry.risk:>2}% | "
                f"Before {before:<16} | After {after:<16} | {outcome}"
            )

        print()
        self.pause()

    def print_full_milestones(self) -> None:
        self.clear_screen()
        print(self.divider("#"))
        print(self.color_text("RUN MILESTONES", Color.BOLD, Color.YELLOW))
        print(self.divider("#"))

        if not self.milestones:
            print(self.color_text("No milestones unlocked yet.", Color.DIM))
            print()
            self.pause()
            return

        for index, milestone in enumerate(self.milestones, start=1):
            print(self.color_text(f"{index}. {milestone.title}", Color.BOLD, Color.MAGENTA))
            print(self.color_text(f"   {milestone.description}", Color.WHITE))
            print()

        self.pause()

    def print_bet_mode_screen(self) -> None:
        self.clear_screen()
        print(self.divider("#"))
        print(self.color_text("BET MODE", Color.BOLD, Color.MAGENTA))
        print(self.divider("#"))
        print(self.color_text(f"Bank available       : {self.format_money(self.save_data['bank_cents'])}", Color.BOLD, Color.GREEN))
        print(self.color_text("Choose a stake from your bank, then choose a payout multiplier.", Color.WHITE))
        print(self.color_text("No upgrades apply in this mode.", Color.CYAN))
        print(self.color_text("Risk formula: higher multiplier = higher loss chance.", Color.DIM))
        print(self.color_text("Reference: x2 = 50% risk.", Color.DIM))
        print()
        print(self.color_text("Examples:", Color.YELLOW))
        print(self.color_text("- x1.10 -> about 9.09% loss chance", Color.WHITE))
        print(self.color_text("- x1.50 -> about 33.33% loss chance", Color.WHITE))
        print(self.color_text("- x2.00 -> 50.00% loss chance", Color.WHITE))
        print(self.color_text("- x6.00 -> about 83.33% loss chance", Color.WHITE))
        print()
        print(self.divider("-"))

    # ------------------------------------------------------------------
    # Menus
    # ------------------------------------------------------------------
    def prompt_main_menu_choice(self) -> str:
        while True:
            choice = input(self.color_text("> ", Color.BOLD)).strip().lower()
            if choice in {"p", "b", "s", "t", "o", "q"}:
                return choice
            print(self.color_text("Invalid choice. Type p, b, s, t, o, or q.", Color.RED))

    def prompt_run_choice(self) -> str:
        while True:
            choice = input(self.color_text("> ", Color.BOLD)).strip().lower()
            if choice in {"r", "c", "h", "m", "x"}:
                return choice
            print(self.color_text("Invalid choice. Type r, c, h, m, or x.", Color.RED))

    def prompt_shop_choice(self) -> str:
        while True:
            choice = input(self.color_text("> ", Color.BOLD)).strip().lower()
            if choice in {"1", "2", "3", "4", "b"}:
                return choice
            print(self.color_text("Invalid choice. Type 1, 2, 3, 4, or b.", Color.RED))

    def prompt_difficulty_choice(self) -> str | None:
        while True:
            self.clear_screen()
            print(self.divider("#"))
            print(self.color_text("SELECT DIFFICULTY", Color.BOLD, Color.YELLOW))
            print(self.divider("#"))
            print(self.color_text("[1] Casual   : lower risk, lower reward", Color.GREEN))
            print(self.color_text("[2] Standard : balanced", Color.WHITE))
            print(self.color_text("[3] Hardcore : higher risk, bigger reward", Color.RED))
            print(self.color_text("[B] Back", Color.YELLOW))
            print(self.divider("-"))
            choice = input(self.color_text("> ", Color.BOLD)).strip().lower()
            if choice == "1":
                return "casual"
            if choice == "2":
                return "standard"
            if choice == "3":
                return "hardcore"
            if choice == "b":
                return None
            print(self.color_text("Invalid choice.", Color.RED))
            self.wait(0.6)

    def prompt_bet_amount(self) -> int | None:
        bank_cents = self.save_data["bank_cents"]
        while True:
            raw = input(self.color_text("Stake amount (example 10 or 10.50, B to go back): ", Color.BOLD)).strip()
            if raw.lower() == "b":
                return None

            stake_cents = self.parse_money_input(raw)
            if stake_cents is None:
                print(self.color_text("Invalid amount.", Color.RED))
                continue

            if stake_cents > bank_cents:
                print(self.color_text("You do not have that much in your bank.", Color.RED))
                continue

            return stake_cents

    def prompt_bet_multiplier(self) -> Decimal | None:
        while True:
            raw = input(self.color_text("Multiplier (example x1.1, x1.5, x2, x6, B to go back): ", Color.BOLD)).strip()
            if raw.lower() == "b":
                return None

            multiplier = self.parse_multiplier_input(raw)
            if multiplier is None:
                print(self.color_text("Invalid multiplier. It must be greater than x1.", Color.RED))
                continue

            return multiplier

    def prompt_bet_confirmation(self) -> str:
        while True:
            choice = input(self.color_text("[Y] Place bet  [N] Change values  [B] Back: ", Color.BOLD)).strip().lower()
            if choice in {"y", "n", "b"}:
                return choice
            print(self.color_text("Invalid choice. Type y, n, or b.", Color.RED))

    # ------------------------------------------------------------------
    # Shop logic
    # ------------------------------------------------------------------
    def can_afford(self, cost_cents: int) -> bool:
        return self.save_data["bank_cents"] >= cost_cents

    def buy_upgrade(self, name: str, cost_cents: int) -> None:
        if not self.can_afford(cost_cents):
            print(self.color_text("Not enough banked money.", Color.RED, Color.BOLD))
            self.pause()
            return

        self.save_data["bank_cents"] -= cost_cents
        self.save_data["upgrades"][name] += 1
        self.save_game()

        if name == "start_amount":
            message = f"Purchased Start Amount. New start: {self.format_money(self.get_start_amount_cents())}"
        elif name == "risk_shield":
            message = f"Purchased Risk Shield. New reduction: -{self.get_upgrade_level('risk_shield')}%"
        elif name == "cash_bonus":
            message = f"Purchased Cash Bonus. New cash-out bonus: +{self.get_cash_bonus_percent()}%"
        else:
            message = "Purchased Revive Token. You now get more second chances per run."

        print(self.color_text(message, Color.GREEN, Color.BOLD))
        self.pause()

    def shop_loop(self) -> None:
        while True:
            self.print_shop()
            choice = self.prompt_shop_choice()

            if choice == "1":
                self.buy_upgrade("start_amount", self.get_start_amount_upgrade_cost())
            elif choice == "2":
                self.buy_upgrade("risk_shield", self.get_risk_shield_upgrade_cost())
            elif choice == "3":
                self.buy_upgrade("cash_bonus", self.get_cash_bonus_upgrade_cost())
            elif choice == "4":
                self.buy_upgrade("revive_token", self.get_revive_upgrade_cost())
            elif choice == "b":
                return

    def options_loop(self) -> None:
        while True:
            self.clear_screen()
            animations = self.get_setting("animations", True)
            print(self.divider("#"))
            print(self.color_text("OPTIONS", Color.BOLD, Color.YELLOW))
            print(self.divider("#"))
            print(self.color_text(f"[1] Toggle animations : {'ON' if animations else 'OFF'}", Color.CYAN))
            print(self.color_text("[B] Back", Color.YELLOW))
            print(self.divider("-"))
            choice = input(self.color_text("> ", Color.BOLD)).strip().lower()
            if choice == "1":
                self.save_data["settings"]["animations"] = not animations
                self.save_game()
            elif choice == "b":
                return

    # ------------------------------------------------------------------
    # Core gameplay - Classic mode
    # ------------------------------------------------------------------
    def suspense(self) -> None:
        lines = [
            self.color_text("You grip the handle...", Color.WHITE),
            self.color_text("The door creaks open...", Color.YELLOW),
            self.color_text("Your heartbeat gets louder...", Color.RED),
        ]

        for line in lines:
            print(line)
            self.wait(0.45)

    def update_lifetime_peak_stats(self) -> None:
        stats = self.save_data["stats"]
        stats["best_run_cents"] = max(stats["best_run_cents"], self.peak_cents)
        stats["best_door"] = max(stats["best_door"], self.door)
        self.save_game()

    def risk_it(self) -> bool:
        risk = self.get_effective_risk_percent(self.door)
        before = self.current_cents
        after = self.next_reward_cents()

        print()
        self.suspense()
        lost = random.random() < (risk / 100)

        if lost:
            revive_pool = self.get_upgrade_level("revive_token")
            if revive_pool > 0 and not self.revive_used:
                self.save_data["upgrades"]["revive_token"] -= 1
                self.revive_used = True
                self.current_cents = max(1, before // 2)
                self.add_history_entry("win", before, self.current_cents)
                self.save_game()
                print(self.color_text("REVIVE TOKEN TRIGGERED! You survived with half your stack.", Color.BOLD, Color.MAGENTA))
                self.pause()
                return True

            self.add_history_entry("lose", before, 0)
            self.save_data["stats"]["classic_losses"] += 1
            self.current_streak = 0
            self.print_lose_screen(before)
            self.update_lifetime_peak_stats()
            self.save_game()
            self.pause()
            return False

        self.current_cents = after
        self.peak_cents = max(self.peak_cents, self.current_cents)
        self.add_history_entry("win", before, after)
        self.current_streak += 1
        self.save_data["stats"]["classic_wins"] += 1
        self.save_data["stats"]["best_win_streak"] = max(self.save_data["stats"]["best_win_streak"], self.current_streak)
        self.door += 1
        self.check_milestones()
        self.maybe_trigger_lucky_event()
        self.update_lifetime_peak_stats()

        print()
        print(self.color_text("*** WIN ***", Color.BOLD, Color.GREEN))
        print(self.color_text(f"You climbed up to {self.format_money(self.current_cents)}.", Color.GREEN))
        self.pause()
        return True

    def cash_out(self) -> None:
        before = self.current_cents
        bonus_percent = self.get_cash_bonus_percent()
        streak_bonus_percent = min(30, self.current_streak * 2)
        total_bonus_percent = bonus_percent + streak_bonus_percent
        bonus_cents = (before * total_bonus_percent) // 100
        payout_cents = before + bonus_cents

        self.add_history_entry("cash", before, payout_cents)
        self.save_data["bank_cents"] += payout_cents
        self.save_data["stats"]["total_cashed_out_cents"] += payout_cents
        self.save_data["stats"]["classic_cashouts"] += 1
        self.update_lifetime_peak_stats()
        self.save_game()

        self.print_cashout_screen(before, bonus_cents, payout_cents, streak_bonus_percent)
        self.pause()

    def print_cashout_screen(self, run_amount_cents: int, bonus_cents: int, payout_cents: int, streak_bonus_percent: int) -> None:
        self.clear_screen()
        print(self.divider("="))
        print(self.color_text("CASHED OUT", Color.BOLD, Color.GREEN))
        print(self.divider("="))
        print(self.color_text(f"Run winnings      : {self.format_money(run_amount_cents)}", Color.WHITE))
        print(self.color_text(f"Cash-out bonus    : {self.format_money(bonus_cents)}", Color.MAGENTA))
        print(self.color_text(f"Streak bonus rate : +{streak_bonus_percent}%", Color.YELLOW))
        print(self.color_text(f"Added to bank     : {self.format_money(payout_cents)}", Color.BOLD, Color.GREEN))
        print(self.color_text(f"Bank total        : {self.format_money(self.save_data['bank_cents'])}", Color.BOLD, Color.CYAN))
        print(self.color_text(f"Door reached      : #{self.door}", Color.YELLOW))
        print(self.color_text(f"Peak this run     : {self.format_money(self.peak_cents)}", Color.WHITE))
        print(self.divider("="))

    def print_lose_screen(self, lost_amount_cents: int) -> None:
        self.clear_screen()
        print(self.divider("="))
        print(self.color_text("BUST", Color.BOLD, Color.RED))
        print(self.divider("="))
        print(self.color_text(f"You lost          : {self.format_money(lost_amount_cents)}", Color.RED))
        print(self.color_text(f"Defeat on door    : #{self.door}", Color.YELLOW))
        print(self.color_text(f"Peak this run     : {self.format_money(self.peak_cents)}", Color.WHITE))
        print(self.color_text(f"Bank stays at     : {self.format_money(self.save_data['bank_cents'])}", Color.CYAN))
        print(self.divider("="))

    def run_loop(self) -> None:
        self.reset_run()
        difficulty = self.prompt_difficulty_choice()
        if difficulty is None:
            return

        self.difficulty_key = difficulty
        self.save_data["stats"]["runs_played"] += 1
        self.save_game()
        self.in_run = True

        while self.in_run and self.running:
            self.print_status()
            self.print_recent_history()
            self.print_recent_milestones()
            self.print_recent_events()
            self.print_run_menu()
            choice = self.prompt_run_choice()

            if choice == "r":
                survived = self.risk_it()
                if not survived:
                    self.in_run = False
            elif choice == "c":
                self.cash_out()
                self.in_run = False
            elif choice == "h":
                self.print_full_history()
            elif choice == "m":
                self.print_full_milestones()
            elif choice == "x":
                self.in_run = False

    # ------------------------------------------------------------------
    # Core gameplay - Bet mode
    # ------------------------------------------------------------------
    def bet_mode_loop(self) -> None:
        while self.running:
            bank_cents = self.save_data["bank_cents"]
            self.print_bet_mode_screen()

            if bank_cents <= 0:
                print(self.color_text("You need money in your bank before you can place a bet.", Color.RED, Color.BOLD))
                print(self.color_text("Play Classic mode first and cash out some money.", Color.YELLOW))
                print()
                self.pause()
                return

            stake_cents = self.prompt_bet_amount()
            if stake_cents is None:
                return

            multiplier = self.prompt_bet_multiplier()
            if multiplier is None:
                continue

            loss_probability = self.multiplier_to_loss_probability(multiplier)
            risk_percent = (loss_probability * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            payout_cents = self.calculate_payout_cents(stake_cents, multiplier)
            profit_cents = payout_cents - stake_cents

            self.clear_screen()
            print(self.divider("#"))
            print(self.color_text("BET PREVIEW", Color.BOLD, Color.MAGENTA))
            print(self.divider("#"))
            print(self.color_text(f"Bank before        : {self.format_money(bank_cents)}", Color.CYAN))
            print(self.color_text(f"Stake              : {self.format_money(stake_cents)}", Color.WHITE))
            print(self.color_text(f"Multiplier         : {self.format_multiplier(multiplier)}", Color.YELLOW))
            print(self.color_text(f"Loss chance        : {self.format_percent_decimal(risk_percent)}", Color.RED))
            print(self.color_text(f"Payout on win      : {self.format_money(payout_cents)}", Color.GREEN))
            print(self.color_text(f"Net profit on win  : {self.format_money(profit_cents)}", Color.GREEN))
            print(self.color_text(f"Loss on fail       : {self.format_money(stake_cents)}", Color.RED))
            print(self.color_text("Upgrades do not affect this mode.", Color.DIM))
            print(self.divider("-"))

            choice = self.prompt_bet_confirmation()
            if choice == "b":
                return
            if choice == "n":
                continue

            self.resolve_bet(stake_cents, multiplier, payout_cents, risk_percent)

    def resolve_bet(self, stake_cents: int, multiplier: Decimal, payout_cents: int, risk_percent: Decimal) -> None:
        print()
        print(self.color_text("Locking in the bet...", Color.WHITE))
        self.wait(0.45)
        print(self.color_text("The wheel is spinning...", Color.YELLOW))
        self.wait(0.55)
        print(self.color_text("Everything hangs on this...", Color.RED))
        self.wait(0.7)

        loss_probability = float(self.multiplier_to_loss_probability(multiplier))
        lost = random.random() < loss_probability
        stats = self.save_data["stats"]
        stats["total_bet_staked_cents"] += stake_cents

        if lost:
            self.save_data["bank_cents"] -= stake_cents
            stats["bet_losses"] += 1
            stats["total_bet_profit_cents"] -= stake_cents
            self.save_game()

            self.clear_screen()
            print(self.divider("="))
            print(self.color_text("BET LOST", Color.BOLD, Color.RED))
            print(self.divider("="))
            print(self.color_text(f"Stake lost         : {self.format_money(stake_cents)}", Color.RED))
            print(self.color_text(f"Multiplier         : {self.format_multiplier(multiplier)}", Color.YELLOW))
            print(self.color_text(f"Loss chance        : {self.format_percent_decimal(risk_percent)}", Color.RED))
            print(self.color_text(f"Bank now           : {self.format_money(self.save_data['bank_cents'])}", Color.CYAN))
            print(self.divider("="))
            self.pause()
            return

        profit_cents = payout_cents - stake_cents
        self.save_data["bank_cents"] += profit_cents
        stats["bet_wins"] += 1
        stats["total_bet_profit_cents"] += profit_cents
        self.save_game()

        self.clear_screen()
        print(self.divider("="))
        print(self.color_text("BET WON", Color.BOLD, Color.GREEN))
        print(self.divider("="))
        print(self.color_text(f"Stake              : {self.format_money(stake_cents)}", Color.WHITE))
        print(self.color_text(f"Multiplier         : {self.format_multiplier(multiplier)}", Color.YELLOW))
        print(self.color_text(f"Loss chance        : {self.format_percent_decimal(risk_percent)}", Color.RED))
        print(self.color_text(f"Payout             : {self.format_money(payout_cents)}", Color.GREEN))
        print(self.color_text(f"Net profit         : {self.format_money(profit_cents)}", Color.GREEN))
        print(self.color_text(f"Bank now           : {self.format_money(self.save_data['bank_cents'])}", Color.CYAN))
        print(self.divider("="))
        self.pause()

    # ------------------------------------------------------------------
    # App loop
    # ------------------------------------------------------------------
    def game_loop(self) -> None:
        while self.running:
            self.print_title_screen()
            choice = self.prompt_main_menu_choice()

            if choice == "p":
                self.run_loop()
            elif choice == "b":
                self.bet_mode_loop()
            elif choice == "s":
                self.shop_loop()
            elif choice == "t":
                self.print_lifetime_stats()
            elif choice == "o":
                self.options_loop()
            elif choice == "q":
                self.clear_screen()
                print(self.color_text("Goodbye.", Color.BOLD, Color.YELLOW))
                self.running = False


def main() -> None:
    random.seed()
    game = LuckyDoorsCLI()

    try:
        game.game_loop()
    except KeyboardInterrupt:
        game.clear_screen()
        print(game.color_text("Interrupted. Goodbye.", Color.BOLD, Color.YELLOW))
        sys.exit(0)


if __name__ == "__main__":
    main()
