#!/usr/bin/env python3
"""
Task Tracker CLI (Session-Based)

Data Model:
  tasks(name TEXT PRIMARY KEY, price REAL NOT NULL)
  work_sessions(
      id INTEGER PRIMARY KEY,
      task_name TEXT NOT NULL,
      start_time TEXT NOT NULL,    -- UTC in ISO-8601
      duration_seconds REAL NOT NULL,
      count INTEGER NOT NULL,
      price REAL NOT NULL,
      FOREIGN KEY (task_name) REFERENCES tasks(name)
  )

Core Behavior:
  - Only create a work_session row when:
       1) user increments (ENTER or typed number)
       2) user switches tasks
       3) user exits the program
  - A "session" is considered completed at that moment,
    so duration + count + price is recorded immutably.

Time Tracking:
  - We keep an in-memory "active session" if a task is active:
      * active_task
      * current_session_start_utc (a datetime)
      * current_session_elapsed (accumulated paused time)
      * paused (bool)
      * timer_start_time (timestamp when last resumed)
  - Pausing/resuming just updates the in-memory time.
  - Switching tasks or incrementing finalizes the old session,
    then starts a brand new session in memory (if continuing).

Price Tracking:
  - We always look up the task's current price at the moment
    a session is finalized, and store that into `work_sessions.price`.

Required Views:
  1) Chronological list of sessions (history)
  2) Daily summary (stats day ...)
  3) Monthly summary (stats month ...)
"""

import sys
import sqlite3
import time
from datetime import datetime, timedelta
import pytz
from typing import Optional
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.table import Table

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

DB_NAME = "tasks.db"
TIMEZONE = pytz.utc  # Adjust if you'd like to display local times differently
console = Console()


# --------------------------------------------------
# DATABASE SETUP
# --------------------------------------------------

def init_db():
    """
    Create or migrate the database schema.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # tasks table
    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        name TEXT PRIMARY KEY,
        price REAL NOT NULL
    );
    """)

    # New: work_sessions table
    c.execute("""
    CREATE TABLE IF NOT EXISTS work_sessions (
        id INTEGER PRIMARY KEY,
        task_name TEXT NOT NULL,
        start_time TEXT NOT NULL,          -- UTC ISO-8601
        duration_seconds REAL NOT NULL,
        count INTEGER NOT NULL,
        price REAL NOT NULL,
        FOREIGN KEY (task_name) REFERENCES tasks(name)
    );
    """)

    conn.commit()
    conn.close()


# --------------------------------------------------
# HELPER: Format Duration
# --------------------------------------------------

def format_duration(seconds: float) -> str:
    """
    Convert a float of seconds into H:MM:SS string.
    """
    if seconds <= 0:
        return "0:00:00"
    td = timedelta(seconds=round(seconds))
    hours, remainder = divmod(int(td.total_seconds()), 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{sec:02d}"


# --------------------------------------------------
# TASK TRACKER
# --------------------------------------------------

class TaskTracker:
    """
    Manages tasks table and the current in-memory session,
    plus finalizing sessions to work_sessions table.
    """

    def __init__(self, db_name=DB_NAME):
        init_db()
        self.conn = sqlite3.connect(db_name)
        self.conn.execute("PRAGMA foreign_keys = ON")

        # Active session tracking:
        self.active_task: Optional[str] = None
        self.current_session_start_utc: Optional[datetime] = None
        self.current_session_elapsed: float = 0.0  # accumulated paused time
        self.paused: bool = True
        self.timer_start_time: float = 0.0  # last time we resumed

    def __del__(self):
        self.conn.close()

    def execute(self, query, params=()):
        c = self.conn.cursor()
        c.execute(query, params)
        self.conn.commit()
        return c

    # -------------------------
    # Tasks
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

    def list_tasks(self):
        c = self.execute("SELECT name, price FROM tasks ORDER BY name")
        return c.fetchall()

    # -------------------------
    # Active Session Management
    # -------------------------

    def start_session_for_task(self, task_name: str):
        """
        Start an in-memory session for the given task.
        If paused=False, we set timer_start_time so we can accumulate real-time.
        """
        self.active_task = task_name
        self.current_session_start_utc = datetime.utcnow()
        self.current_session_elapsed = 0.0
        self.paused = True
        self.timer_start_time = 0.0

    def get_current_elapsed(self) -> float:
        """
        Return how much time has been accumulated in the current active session (in seconds).
        """
        if not self.active_task or not self.current_session_start_utc:
            return 0.0
        elapsed = self.current_session_elapsed
        if not self.paused:
            elapsed += (time.time() - self.timer_start_time)
        return elapsed

    def finalize_session(self, count: int = 1):
        """
        Finalize the current in-memory session by inserting a row
        into work_sessions. Then reset active_task to None.
        If there is no active task, do nothing.
        Returns whether the old session was paused or not,
        so we can preserve that state if we want to start a new session.
        """
        if not self.active_task or not self.current_session_start_utc:
            return self.paused  # No active session to finalize

        # Calculate final duration
        final_duration = self.get_current_elapsed()

        # Grab current price
        price_now = self.get_task_price(self.active_task) or 0.0

        # Insert session
        self.execute("""
            INSERT INTO work_sessions (task_name, start_time, duration_seconds, count, price)
            VALUES (?, ?, ?, ?, ?)
        """, (
            self.active_task,
            self.current_session_start_utc.isoformat(),  # store as UTC ISO string
            final_duration,
            count,
            price_now
        ))

        old_paused = self.paused

        # Reset the active session entirely
        self.active_task = None
        self.current_session_start_utc = None
        self.current_session_elapsed = 0.0
        self.timer_start_time = 0.0
        self.paused = True

        return old_paused

    # -------------------------
    # Commands: start/pause/switch/increment/exit
    # -------------------------

    def start(self):
        if not self.active_task:
            console.print("[red]No active task to start. Use 'switch <task>' first.[/]")
            return
        if not self.paused:
            console.print("[yellow]Timer already running.[/]")
            return

        # Resume tracking
        self.paused = False
        self.timer_start_time = time.time()
        console.print("[green]Session resumed/started[/]")

    def pause(self):
        if not self.active_task:
            console.print("[red]No active task to pause.[/]")
            return
        if self.paused:
            console.print("[yellow]Already paused.[/]")
            return

        # Accumulate the elapsed time
        self.current_session_elapsed += (time.time() - self.timer_start_time)
        self.paused = True
        console.print("[green]Session paused[/]")

    def switch_task(self, new_task: str):
        """
        If a session is active, finalize it with count=1.
        Then start a new session for the new task,
        preserving paused/unpaused state.
        """
        # Ensure the new task actually exists or can be found in tasks table
        price = self.get_task_price(new_task)
        if price is None:
            console.print(f"[red]Task '{new_task}' not found. Use 'set-price <task> <price>' first.[/]")
            return

        old_paused = self.paused
        if self.active_task:
            # Finalize the old session (count=1 by default)
            old_paused = self.finalize_session(count=1)

        # Now start a new in-memory session for the new task
        self.start_session_for_task(new_task)

        # If we were running before, continue running
        if not old_paused:
            self.paused = False
            self.timer_start_time = time.time()

        console.print(f"[cyan]Switched active task to '{new_task}'[/]")

    def increment_current_task(self, n: int = 1):
        """
        When user presses ENTER (n=1) or types a number (n),
        finalize the current in-memory session for the active task,
        then start a brand-new session for the same task
        preserving paused/unpaused state.
        """
        if not self.active_task:
            console.print("[red]No active task to increment.[/]")
            return

        # Finalize old session
        old_paused = self.finalize_session(count=n)

        # Start a fresh session for the same task
        same_task = self.active_task  # it was reset in finalize_session
        # Actually we need to recall what the old active task was
        # before finalize_session wiped it out:
        # We'll do that in finalize_session return value or store it ahead of time
        # Simpler: store it now:
        old_task = self.active_task  # but it's already None after finalize? Let's do it earlier
        # Instead, let's do:
        # We'll do this approach:
        # We'll just keep a local variable before finalizing:

    def increment_current_task(self, n: int = 1, _placeholder=None):
        """
        Overwrite of the above method to fix scoping:
        """
        if not self.active_task:
            console.print("[red]No active task to increment.[/]")
            return

        current_task = self.active_task  # remember it
        old_paused = self.finalize_session(count=n)
        # finalize_session clears self.active_task

        # Create a new session for the same task
        self.start_session_for_task(current_task)

        if not old_paused:
            # if we were running, keep running
            self.paused = False
            self.timer_start_time = time.time()

        console.print(f"[green]Incremented '{current_task}' by {n}[/]")

    def handle_exit(self):
        """
        On exit, if there's an active session with any real time,
        finalize it with count=1. Then exit.
        """
        if self.active_task and self.current_session_start_utc:
            # Finalize with count=1
            self.finalize_session(count=1)
        console.print("[bold]Exiting...[/bold]")

    # ------------------------------------------------
    # Reporting: Chronological, Daily, Monthly
    # ------------------------------------------------

    def fetch_all_sessions(self):
        """
        Return all sessions as a list of tuples:
         (id, task_name, start_time(iso), duration_seconds, count, price)
        ordered by start_time ASC.
        """
        c = self.execute("""
            SELECT id, task_name, start_time, duration_seconds, count, price
            FROM work_sessions
            ORDER BY start_time ASC
        """)
        return c.fetchall()

    def show_chronological_view(self, limit: Optional[int] = None):
        """
        Print a chronological listing of sessions (by start_time ascending).
        If limit is given, we show only the most recent N by date descending.
        """
        rows = self.fetch_all_sessions()
        if not rows:
            console.print("[magenta]No sessions found.[/]")
            return

        # If limit is specified, show the *latest* N, so we might need to sort desc and slice
        if limit is not None and limit > 0:
            rows = sorted(rows, key=lambda r: r[2], reverse=True)[:limit]
            rows = sorted(rows, key=lambda r: r[2])  # re-sort ascending for display

        table = Table(title="Chronological Sessions", header_style="bold magenta")
        table.add_column("Start (Local)", style="cyan")
        table.add_column("Task")
        table.add_column("Duration", justify="right")
        table.add_column("Count", justify="right")
        table.add_column("Price(€)", justify="right")
        table.add_column("Earned(€)", justify="right")

        for (sid, task, start_iso, dur, ccount, price) in rows:
            # Convert start_iso (UTC) -> local time
            utc_dt = datetime.fromisoformat(start_iso)
            local_dt = pytz.utc.localize(utc_dt).astimezone(TIMEZONE)
            local_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
            earned = ccount * price
            table.add_row(
                local_str,
                task,
                format_duration(dur),
                str(ccount),
                f"{price:.2f}",
                f"{earned:.2f}"
            )

        console.print(table)

    def show_daily_summary(self, day_str: Optional[str] = None):
        """
        Show an aggregated daily summary from work_sessions.
        If day_str is None, use today's date in local time.
        Aggregation = sum duration, sum count, sum earned by task.
        """
        if day_str is None:
            # use local "today"
            now_local = datetime.now(TIMEZONE)
            day_str = now_local.strftime("%Y-%m-%d")

        # Convert that local day to a 00:00 local -> UTC range
        local_day_start = datetime.strptime(day_str, "%Y-%m-%d")
        local_day_start = TIMEZONE.localize(local_day_start)
        local_day_end = local_day_start + timedelta(days=1)
        # Convert to UTC
        day_start_utc = local_day_start.astimezone(pytz.utc)
        day_end_utc = local_day_end.astimezone(pytz.utc)

        # Fetch relevant sessions
        c = self.execute("""
            SELECT task_name, duration_seconds, count, price, start_time
            FROM work_sessions
            WHERE start_time >= ? AND start_time < ?
            ORDER BY task_name
        """, (day_start_utc.isoformat(), day_end_utc.isoformat()))
        rows = c.fetchall()

        if not rows:
            console.print(f"[bold magenta]No sessions found for day {day_str}[/]")
            return

        # Aggregate by task
        aggregate = {}
        for (task, dur, cnt, p, st) in rows:
            if task not in aggregate:
                aggregate[task] = {
                    'count': 0,
                    'duration': 0.0,
                    'earned': 0.0
                }
            aggregate[task]['count'] += cnt
            aggregate[task]['duration'] += dur
            aggregate[task]['earned'] += (cnt * p)

        table = Table(title=f"Daily Summary for {day_str}", header_style="bold magenta")
        table.add_column("Task", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Earned (€)", justify="right")
        table.add_column("Hourly Rate (€ / hr)", justify="right")

        total_count = 0
        total_time = 0.0
        total_earned = 0.0

        for task in sorted(aggregate.keys()):
            ccount = aggregate[task]['count']
            dur = aggregate[task]['duration']
            earned = aggregate[task]['earned']
            hours = dur / 3600 if dur > 0 else 0
            hourly_rate = earned / hours if hours > 0 else 0
            table.add_row(
                task,
                str(ccount),
                format_duration(dur),
                f"{earned:.2f}",
                f"{hourly_rate:.2f}"
            )
            total_count += ccount
            total_time += dur
            total_earned += earned

        tot_hours = total_time / 3600 if total_time > 0 else 0
        tot_hrate = total_earned / tot_hours if tot_hours > 0 else 0
        table.add_row(
            "[bold]TOTAL[/bold]",
            str(total_count),
            format_duration(total_time),
            f"[bold]{total_earned:.2f}[/bold]",
            f"[bold]{tot_hrate:.2f}[/bold]"
        )

        console.print(table)

    def show_monthly_summary(self, ym_str: Optional[str] = None):
        """
        Show aggregated monthly summary. If ym_str is None, use current month (local).
        Format of ym_str = YYYY-MM
        """
        if ym_str is None:
            now_local = datetime.now(TIMEZONE)
            ym_str = now_local.strftime("%Y-%m")

        # parse year-month
        year, month = ym_str.split("-")
        year = int(year)
        month = int(month)
        local_month_start = datetime(year, month, 1)
        local_month_start = TIMEZONE.localize(local_month_start)
        # next month
        next_month = month + 1
        next_year = year
        if next_month == 13:
            next_month = 1
            next_year += 1
        local_month_end = datetime(next_year, next_month, 1)
        local_month_end = TIMEZONE.localize(local_month_end)

        month_start_utc = local_month_start.astimezone(pytz.utc)
        month_end_utc = local_month_end.astimezone(pytz.utc)

        c = self.execute("""
            SELECT task_name, duration_seconds, count, price, start_time
            FROM work_sessions
            WHERE start_time >= ? AND start_time < ?
            ORDER BY task_name
        """, (month_start_utc.isoformat(), month_end_utc.isoformat()))
        rows = c.fetchall()

        if not rows:
            console.print(f"[bold magenta]No sessions found for month {ym_str}[/]")
            return

        aggregate = {}
        for (task, dur, cnt, p, st) in rows:
            if task not in aggregate:
                aggregate[task] = {
                    'count': 0,
                    'duration': 0.0,
                    'earned': 0.0
                }
            aggregate[task]['count'] += cnt
            aggregate[task]['duration'] += dur
            aggregate[task]['earned'] += (cnt * p)

        table = Table(title=f"Monthly Summary for {ym_str}", header_style="bold magenta")
        table.add_column("Task", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Earned (€)", justify="right")
        table.add_column("Hourly Rate (€ / hr)", justify="right")

        total_count = 0
        total_time = 0.0
        total_earned = 0.0

        for task in sorted(aggregate.keys()):
            ccount = aggregate[task]['count']
            dur = aggregate[task]['duration']
            earned = aggregate[task]['earned']
            hours = dur / 3600 if dur > 0 else 0
            hr = earned / hours if hours > 0 else 0
            table.add_row(
                task,
                str(ccount),
                format_duration(dur),
                f"{earned:.2f}",
                f"{hr:.2f}"
            )
            total_count += ccount
            total_time += dur
            total_earned += earned

        tot_hours = total_time / 3600 if total_time > 0 else 0
        tot_hrate = total_earned / tot_hours if tot_hours > 0 else 0
        table.add_row(
            "[bold]TOTAL[/bold]",
            str(total_count),
            format_duration(total_time),
            f"[bold]{total_earned:.2f}[/bold]",
            f"[bold]{tot_hrate:.2f}[/bold]"
        )

        console.print(table)


# --------------------------------------------------
# MAIN CLI
# --------------------------------------------------

def main():
    tracker = TaskTracker()
    style = Style.from_dict({'prompt': 'ansicyan bold'})

    def get_prompt_text():
        """
        Dynamically build the prompt label showing:
          - active_task
          - if paused vs running
          - current elapsed time
        """
        if tracker.active_task:
            elapsed = tracker.get_current_elapsed()
            if tracker.paused:
                label = f"[<red>■</red> {tracker.active_task}]"
            else:
                hhmmss = format_duration(elapsed)
                label = f"[<green>●</green> {tracker.active_task} {hhmmss}]"
        else:
            label = "[<red>■</red> no-task]"
        return HTML(f"<b>{label}</b> ➜ ")

    session = PromptSession()

    while True:
        try:
            command_line = session.prompt(
                get_prompt_text,
                style=style,
                refresh_interval=1.0 if (tracker.active_task and not tracker.paused) else None
            ).strip()
        except (KeyboardInterrupt, EOFError):
            tracker.handle_exit()
            break

        if not command_line:
            # ENTER with no command => increment by 1
            tracker.increment_current_task(1)
            continue

        parts = command_line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        # If the entire command is a number => increment
        if cmd.isdigit():
            n = int(cmd)
            tracker.increment_current_task(n)
            continue

        if cmd in ("exit", "quit"):
            tracker.handle_exit()
            break

        elif cmd == "help":
            console.print("""
[bold]Commands:[/bold]
  [cyan]switch <task>[/cyan]
      Switch to a different task. Finalizes the old session (if any).
  [cyan]start[/cyan] / [cyan]s[/cyan]
      Start or resume the timer for the current task.
  [cyan]pause[/cyan] / [cyan]p[/cyan]
      Pause the timer for the current task.
  [cyan]<number>[/cyan] (e.g. "5")
      Finalize the current session with count=<number>, then start a new session.
  [cyan]set-price <task> <price>[/cyan]
      Create or update the price of a task.
  [cyan]list[/cyan]
      List all known tasks and their prices.
  [cyan]status[/cyan]
      Show today's daily summary (all tasks).
  [cyan]stats day [YYYY-MM-DD][/cyan]
      Show daily summary for a given day (defaults to today).
  [cyan]stats month [YYYY-MM][/cyan]
      Show monthly summary for a given month (defaults to current month).
  [cyan]history [n][/cyan]
      Show chronological sessions. If n is given, show only the last n sessions.
  [cyan]help[/cyan]
      Show this help message.
  [cyan]exit[/cyan] / [cyan]quit[/cyan]
      Exit the program.

[dim]Press ENTER with no command to increment the active task by 1.[/dim]
            """)
            continue

        elif cmd in ("start", "s"):
            tracker.start()

        elif cmd in ("pause", "p"):
            tracker.pause()

        elif cmd == "switch":
            if len(args) < 1:
                console.print("[red]Usage: switch <task_name>[/]")
                continue
            tracker.switch_task(args[0])

        elif cmd == "set-price":
            if len(args) < 2:
                console.print("[red]Usage: set-price <task> <price>[/]")
                continue
            tname = args[0]
            try:
                pval = float(args[1])
            except ValueError:
                console.print("[red]Invalid price. Must be a number.[/]")
                continue
            tracker.set_task_price(tname, pval)

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
            # Show today's daily summary
            tracker.show_daily_summary(None)

        elif cmd == "stats":
            if not args:
                # Show today's daily + this month's summary
                tracker.show_daily_summary(None)
                tracker.show_monthly_summary(None)
                continue
            # If we have at least 1 argument
            mode = args[0]
            if mode == "day":
                dstr = args[1] if len(args) > 1 else None
                tracker.show_daily_summary(dstr)
            elif mode == "month":
                mstr = args[1] if len(args) > 1 else None
                tracker.show_monthly_summary(mstr)
            else:
                console.print("[red]Usage: stats <day|month> [YYYY-MM-DD|YYYY-MM][/]")
                continue

        elif cmd == "history":
            limit = None
            if len(args) == 1:
                try:
                    limit = int(args[0])
                except ValueError:
                    console.print("[red]Invalid limit. Must be an integer.[/]")
            tracker.show_chronological_view(limit)

        else:
            console.print(f"[red]Unknown command:[/] {cmd} (try 'help')")


if __name__ == "__main__":
    main()
