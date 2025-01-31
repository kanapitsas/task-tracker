#!/usr/bin/env python3
"""
Task Tracker CLI with Per-Increment Logging

Stores:
  - A 'tasks' table: (name, price)
  - A 'daily_logs' table: (date, task_name, count, total_time_seconds)

Workflow:
  - "switch <task>" picks which task is active
  - "start" begins counting time
  - "pause" stops counting time
  - Pressing ENTER increments count for the active task by 1
  - Typing a number increments count by that number
  - Each increment is written to daily_logs for today's date,
    and time is only updated when you 'pause' or 'switch' tasks
    (i.e. we add the elapsed time to that day's row).

Commands:
  help, list, set-price, switch, start, pause, status,
  <number>, stats, history, exit
"""
import sys
import sqlite3
from datetime import datetime, timedelta
import time
import pytz
from typing import Optional
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------

DB_NAME = "tasks.db"
# Adjust to your local time zone if desired:
TIMEZONE = pytz.utc
console = Console()

# ---------------------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------------------

def init_db():
    """
    Create or migrate the database schema.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Tasks table
    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        name TEXT PRIMARY KEY,
        price REAL NOT NULL
    );
    """)

    # daily_logs table: row per (date, task_name), with aggregated count & total_time
    c.execute("""
    CREATE TABLE IF NOT EXISTS daily_logs (
        log_date TEXT NOT NULL,
        task_name TEXT NOT NULL,
        count INTEGER NOT NULL,
        total_time_seconds REAL NOT NULL,
        PRIMARY KEY (log_date, task_name),
        FOREIGN KEY (task_name) REFERENCES tasks(name)
    );
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------
# HELPER: format duration
# ---------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    """
    Convert a float of seconds into HH:MM:SS string.
    """
    if seconds <= 0:
        return "0:00:00"
    td = timedelta(seconds=round(seconds))
    hours, remainder = divmod(int(td.total_seconds()), 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{sec:02d}"


# ---------------------------------------------------------------------
# TASK TRACKER
# ---------------------------------------------------------------------

class TaskTracker:
    """
    Manages:
      - known tasks in 'tasks' table
      - daily usage in 'daily_logs'
    """

    def __init__(self, db_name=DB_NAME):
        init_db()
        self.conn = sqlite3.connect(db_name)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.active_task: Optional[str] = None
        self.paused = True
        self.start_time: float = 0.0  # for time tracking
        self.session_elapsed: float = 0.0  # accumulated time for current task session

    def __del__(self):
        self.conn.close()

    def execute(self, query, params=()):
        c = self.conn.cursor()
        c.execute(query, params)
        self.conn.commit()
        return c

    # -------------------------
    # Task table management
    # -------------------------
    def set_task_price(self, name: str, price: float):
        self.execute("""
            INSERT OR REPLACE INTO tasks (name, price) VALUES (?, ?)
        """, (name, price))
        console.print(f"[green]Task '{name}' price set to €{price:.2f}[/]")

    def get_task_price(self, name: str) -> Optional[float]:
        c = self.execute("SELECT price FROM tasks WHERE name=?;", (name,))
        row = c.fetchone()
        return row[0] if row else None

    def get_today_count(self, task_name: str) -> int:
        """Get the count for a task for today"""
        today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        c = self.execute("""
            SELECT count FROM daily_logs
            WHERE log_date = ? AND task_name = ?
        """, (today_str, task_name))
        row = c.fetchone()
        return row[0] if row else 0

    def list_tasks(self):
        c = self.execute("SELECT name, price FROM tasks ORDER BY name")
        return c.fetchall()

    # -------------------------
    # daily_logs management
    # -------------------------

    def upsert_daily_log(self, date_str: str, task_name: str, count_delta: int, time_delta: float):
        """
        Add count_delta and time_delta to the daily_logs row for (date_str, task_name).
        If none exists, create it.
        """
        cur = self.execute("""
            SELECT count, total_time_seconds
            FROM daily_logs
            WHERE log_date = ? AND task_name = ?
        """, (date_str, task_name))
        row = cur.fetchone()
        if row:
            old_count, old_time = row
            new_count = old_count + count_delta
            new_time = old_time + time_delta
            self.execute("""
                UPDATE daily_logs
                SET count = ?, total_time_seconds = ?
                WHERE log_date = ? AND task_name = ?
            """, (new_count, new_time, date_str, task_name))
        else:
            self.execute("""
                INSERT INTO daily_logs (log_date, task_name, count, total_time_seconds)
                VALUES (?, ?, ?, ?)
            """, (date_str, task_name, count_delta, time_delta))

    # -------------------------
    # Time tracking
    # -------------------------

    def start(self):
        if not self.paused:
            console.print("[red]Already started[/]")
            return
        if not self.active_task:
            console.print("[red]No task is active to start.[/]")
            return
        self.paused = False
        self.start_time = time.time()
        console.print("[green]Session started[/]")

    def pause(self):
        if self.paused:
            console.print("[yellow]Already paused[/]")
            return
        # Add elapsed time to session total
        self.session_elapsed += time.time() - self.start_time
        self.paused = True
        console.print("[green]Session paused[/]")

    def switch_task(self, new_task: str):
        """
        If we're running, first update time for the old task, then switch.
        """
        price = self.get_task_price(new_task)
        if price is None:
            console.print(f"[red]Task '{new_task}' not found. Use 'set-price <task> <price>' first.[/]")
            return

        # If there's an active task and we are running, add the elapsed time
        if self.active_task:
            if not self.paused:
                self.session_elapsed += time.time() - self.start_time
                self.add_time_to_task(self.active_task, self.session_elapsed)
                self.session_elapsed = 0.0  # Reset after adding time

        self.active_task = new_task
        if not self.paused:
            self.start_time = time.time()  # reset the baseline
        console.print(f"[cyan]Switched active task to '{new_task}'[/]")

    def get_current_elapsed(self) -> float:
        """Get total elapsed time for current task session"""
        if self.paused:
            return self.session_elapsed
        return self.session_elapsed + (time.time() - self.start_time)

    def add_time_to_task(self, task_name: str, elapsed_sec: float):
        """
        Add elapsed_sec to the daily log for task_name (today).
        """
        today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        self.upsert_daily_log(today_str, task_name, 0, elapsed_sec)

    def increment_current_task(self, n=1):
        """
        Increment the daily logs by n for the active task. This is akin to
        pressing ENTER in your original code. The user can do `i 5` or just press ENTER for `i 1`.

        **Modified Behavior:**
        - If the timer is running, add the elapsed time to the daily_logs.
        - Reset the session_elapsed and start_time.
        """
        if not self.active_task:
            console.print("[red]No active task to increment.[/]")
            return

        # If the timer is running, add the elapsed time to daily_logs
        if not self.paused:
            elapsed = self.get_current_elapsed()
            self.add_time_to_task(self.active_task, elapsed)
            self.session_elapsed = 0.0
            self.start_time = time.time()  # Reset start time for continued timing

        # Store in daily_logs for today
        today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        self.upsert_daily_log(today_str, self.active_task, n, 0.0)
        console.print(f"[green]Incremented '{self.active_task}' by {n}[/]")

    # -------------------------
    # Statistics
    # -------------------------

    def get_stats_day(self, date_str: Optional[str] = None):
        """
        Return stats for a single day. If date_str not provided, use today's date.
        """
        if not date_str:
            date_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        c = self.execute("""
            SELECT dl.task_name,
                   dl.count,
                   dl.total_time_seconds,
                   IFNULL(t.price, 0.0) as current_price,
                   dl.count * IFNULL(t.price, 0.0) as total_earned
            FROM daily_logs dl
            LEFT JOIN tasks t ON dl.task_name = t.name
            WHERE dl.log_date = ?
            ORDER BY dl.task_name
        """, (date_str,))
        return (date_str, c.fetchall())

    def get_stats_month(self, year_month: Optional[str] = None):
        """
        Return stats for a given month YYYY-MM. If not provided, use current month.
        We'll sum the daily logs for that month.
        """
        if not year_month:
            year_month = datetime.now(TIMEZONE).strftime("%Y-%m")
        # We do a LIKE 'YYYY-MM%' match
        c = self.execute("""
            SELECT dl.task_name,
                   SUM(dl.count),
                   SUM(dl.total_time_seconds),
                   IFNULL(t.price, 0.0) as current_price,
                   SUM(dl.count) * IFNULL(t.price, 0.0) as total_earned
            FROM daily_logs dl
            LEFT JOIN tasks t ON dl.task_name = t.name
            WHERE dl.log_date LIKE ?
            GROUP BY dl.task_name
            ORDER BY dl.task_name
        """, (year_month + "%",))
        return (year_month, c.fetchall())

    def get_history(self, n: Optional[int] = None):
        """
        Retrieve the last n entries from daily_logs ordered by date descending.
        If n is None, retrieve all entries for today.
        """
        today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        if n is None:
            c = self.execute("""
                SELECT log_date, task_name, count, total_time_seconds
                FROM daily_logs
                WHERE log_date = ?
                ORDER BY log_date DESC, task_name ASC
            """, (today_str,))
        else:
            c = self.execute("""
                SELECT log_date, task_name, count, total_time_seconds
                FROM daily_logs
                ORDER BY log_date DESC, task_name ASC
                LIMIT ?
            """, (n,))
        return c.fetchall()


# ---------------------------------------------------------------------
# DISPLAY UTILS
# ---------------------------------------------------------------------

def show_stats_day(tracker: TaskTracker, day: Optional[str]):
    date_str, rows = tracker.get_stats_day(day)
    if not rows:
        console.print(f"[bold magenta]No data found for day {date_str}[/]")
        return

    table = Table(title=f"Stats for {date_str}", header_style="bold magenta")
    table.add_column("Task", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Time (H:MM:SS)", justify="right")
    table.add_column("Price (€)", justify="right")
    table.add_column("Total Earned (€)", justify="right")
    table.add_column("Hourly Rate (€ / hr)", justify="right")

    total_earned = 0.0
    total_time = 0.0
    total_count = 0

    for (task_name, count, time_sec, price, earned) in rows:
        hours = time_sec / 3600 if time_sec > 0 else 0
        hourly_rate = earned / hours if hours > 0 else 0.0
        table.add_row(
            str(task_name),
            str(count),
            format_duration(time_sec),
            f"{price:.2f}",
            f"{earned:.2f}",
            f"{hourly_rate:.2f}"
        )
        total_earned += earned
        total_time += time_sec
        total_count += count

    total_hours = total_time / 3600 if total_time > 0 else 0
    total_hourly_rate = total_earned / total_hours if total_hours > 0 else 0.0

    # Summaries
    table.add_row(
        "[bold]TOTAL[/bold]",
        str(total_count),
        format_duration(total_time),
        "",
        f"[bold]{total_earned:.2f}[/bold]",
        f"[bold]{total_hourly_rate:.2f}[/bold]"
    )
    console.print(table)


def show_stats_month(tracker: TaskTracker, ym: Optional[str]):
    year_month, rows = tracker.get_stats_month(ym)
    if not rows:
        console.print(f"[bold magenta]No data found for month {year_month}[/]")
        return

    table = Table(title=f"Stats for {year_month}", header_style="bold magenta")
    table.add_column("Task", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Time (H:MM:SS)", justify="right")
    table.add_column("Price (€)", justify="right")
    table.add_column("Total Earned (€)", justify="right")
    table.add_column("Hourly Rate (€ / hr)", justify="right")

    total_earned = 0.0
    total_time = 0.0
    total_count = 0

    for (task_name, sum_count, sum_time, price, sum_earned) in rows:
        hours = sum_time / 3600 if sum_time > 0 else 0
        hourly_rate = sum_earned / hours if hours > 0 else 0.0
        table.add_row(
            str(task_name),
            str(sum_count),
            format_duration(sum_time),
            f"{price:.2f}",
            f"{sum_earned:.2f}",
            f"{hourly_rate:.2f}"
        )
        total_earned += sum_earned
        total_time += sum_time
        total_count += sum_count

    total_hours = total_time / 3600 if total_time > 0 else 0
    total_hourly_rate = total_earned / total_hours if total_hours > 0 else 0.0

    table.add_row(
        "[bold]TOTAL[/bold]",
        str(total_count),
        format_duration(total_time),
        "",
        f"[bold]{total_earned:.2f}[/bold]",
        f"[bold]{total_hourly_rate:.2f}[/bold]"
    )
    console.print(table)


def show_history(tracker: TaskTracker, n: Optional[int]):
    rows = tracker.get_history(n)
    if not rows:
        if n is None:
            today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
            console.print(f"[bold magenta]No data found for today ({today_str})[/]")
        else:
            console.print(f"[bold magenta]No history entries found.[/]")
        return

    table = Table(title="History", header_style="bold magenta")
    table.add_column("Date", style="cyan")
    table.add_column("Task", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Time (H:MM:SS)", justify="right")

    for (log_date, task_name, count, time_sec) in rows:
        table.add_row(
            log_date,
            task_name,
            str(count),
            format_duration(time_sec)
        )

    console.print(table)


# ---------------------------------------------------------------------
# MAIN CLI
# ---------------------------------------------------------------------

def main():
    tracker = TaskTracker()
    style = Style.from_dict({'prompt': 'ansicyan bold'})

    def get_prompt_text():
        if tracker.active_task:
            count = tracker.get_today_count(tracker.active_task)
            if tracker.paused:
                prompt_label = f"[<red>■</red> {tracker.active_task} ({count})]"
            else:
                elapsed = tracker.get_current_elapsed()
                hhmmss = format_duration(elapsed)
                prompt_label = f"[<green>●</green> {tracker.active_task} ({count}) {hhmmss}]"
        else:
            prompt_label = "[<red>■</red> no-task]"
        return HTML(f"<b>{prompt_label}</b> ➜ ")

    while True:
        try:
            # Use refresh_interval to update the prompt regularly when timer is running
            command_line = PromptSession().prompt(
                get_prompt_text,
                style=style,
                refresh_interval=1.0 if not tracker.paused and tracker.active_task else None
            ).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold]Exiting...[/bold]")
            break

        # Rest of the command processing remains the same
        if not command_line:
            tracker.increment_current_task(1)
            continue

        parts = command_line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        # Check if the entire command is a number for incrementing
        if cmd.isdigit():
            increment_value = int(cmd)
            tracker.increment_current_task(increment_value)
            continue

        if cmd in ("exit", "quit"):
            console.print("Goodbye!")
            break

        elif cmd == "help":
            console.print("""
[bold]Commands:[/bold]
  [cyan]switch <task>[/cyan]                  Switch active task (and pause old one if needed).
  [cyan]start[/cyan] / [cyan]s[/cyan]                Start the timer for the current task.
  [cyan]pause[/cyan] / [cyan]p[/cyan]                Pause the timer.
  [cyan]<number>[/cyan]                       Increment the current task count by the specified number.
  [cyan]set-price <task> <price>[/cyan]        Create/update the price of a task.
  [cyan]list[/cyan]                           List known tasks.
  [cyan]status[/cyan]                         Show total time and count for each task for today.
  [cyan]stats[/cyan]                          Show stats for today and current month.
  [cyan]history <n>[/cyan]                     Show the last n recorded entries. If no number is provided, show all entries for today.
  [cyan]help[/cyan]                           Show this help message.
  [cyan]exit[/cyan] / [cyan]quit[/cyan]               Exit the program.

Press [Enter] with no command to increment the active task by 1.
            """)
            continue

        elif cmd == "switch":
            if len(args) < 1:
                console.print("[red]Usage: switch <task_name>[/]")
                continue
            tracker.switch_task(args[0])

        elif cmd in ("start", "s"):
            tracker.start()

        elif cmd in ("pause", "p"):
            tracker.pause()

        elif cmd in ("set-price"):
            if len(args) < 2:
                console.print("[red]Usage: set-price <task> <price>[/]")
                continue
            task_name = args[0]
            try:
                price_val = float(args[1])
                if price_val < 0:
                    raise ValueError
            except ValueError:
                console.print("[red]Invalid price. Please provide a non-negative number.[/]")
                continue
            tracker.set_task_price(task_name, price_val)

        elif cmd == "list":
            tasks = tracker.list_tasks()
            if not tasks:
                console.print("[yellow]No tasks found.[/]")
                continue
            table = Table(title="Known Tasks", header_style="bold blue")
            table.add_column("Name", style="cyan")
            table.add_column("Price (€)", justify="right")
            for (tn, pr) in tasks:
                table.add_row(tn, f"{pr:.2f}")
            console.print(table)

        elif cmd == "status":
            # Show total time and count for each task for today
            today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
            c = tracker.execute("""
                SELECT dl.task_name,
                       dl.count,
                       dl.total_time_seconds,
                       IFNULL(t.price, 0.0) as current_price,
                       dl.count * IFNULL(t.price, 0.0) as total_earned
                FROM daily_logs dl
                LEFT JOIN tasks t ON dl.task_name = t.name
                WHERE dl.log_date = ?
                ORDER BY dl.task_name
            """, (today_str,))
            rows = c.fetchall()
            if not rows:
                console.print(f"[yellow]No data for today ({today_str}).[/]")
                continue

            table = Table(title=f"Status for {today_str}", header_style="bold magenta")
            table.add_column("Task", style="cyan")
            table.add_column("Count", justify="right")
            table.add_column("Time (H:MM:SS)", justify="right")
            table.add_column("Total Earned (€)", justify="right")

            total_earned = 0.0
            total_time = 0.0
            total_count = 0

            for (task_name, count, time_sec, price, earned) in rows:
                table.add_row(
                    str(task_name),
                    str(count),
                    format_duration(time_sec),
                    f"{earned:.2f}"
                )
                total_earned += earned
                total_time += time_sec
                total_count += count

            table.add_row(
                "[bold]TOTAL[/bold]",
                str(total_count),
                format_duration(total_time),
                f"[bold]{total_earned:.2f}[/bold]"
            )
            console.print(table)

        elif cmd == "stats":
            if not args:
                # Show both day and month stats
                show_stats_day(tracker, None)
                show_stats_month(tracker, None)
                continue
            mode = args[0]
            date_arg = args[1] if len(args) > 1 else None
            if mode == "day":
                show_stats_day(tracker, date_arg)
            elif mode == "month":
                show_stats_month(tracker, date_arg)
            else:
                console.print("[red]Usage: stats <day|month> [YYYY-MM-DD|YYYY-MM][/]")

        elif cmd == "history":
            if len(args) >= 1:
                try:
                    n = int(args[0])
                    if n < 1:
                        raise ValueError
                except ValueError:
                    console.print("[red]Invalid number. Please provide a positive integer.[/]")
                    continue
            else:
                n = None
            show_history(tracker, n)

        else:
            console.print(f"[red]Unknown command:[/] {cmd} (try 'help')")


if __name__ == "__main__":
    main()
