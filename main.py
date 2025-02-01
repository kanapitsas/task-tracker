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

    # work_sessions table without ON DELETE CASCADE
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
        """
        List all tasks with their price and last used date,
        sorted by last used date (recent last).
        """
        c = self.execute("""
            SELECT tasks.name, tasks.price, MAX(work_sessions.start_time) as last_used
            FROM tasks
            LEFT JOIN work_sessions ON tasks.name = work_sessions.task_name
            GROUP BY tasks.name
            ORDER BY last_used ASC
        """)
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
        self.start_session_for_task(new_task)

        # If we were running before, continue running
        if not old_paused:
            self.paused = False
            self.timer_start_time = time.time()

        console.print(f"[cyan]Switched active task to '{new_task}'[/]")

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
        table.add_column("ID", style="yellow", justify="right")
        table.add_column("Start (Local)", style="cyan")
        table.add_column("Task")
        table.add_column("Duration", justify="right")
        table.add_column("Count", justify="right")
        table.add_column("Price (€)", justify="right")
        table.add_column("Earned (€)", justify="right")

        for (sid, task, start_iso, dur, ccount, price) in rows:
            # Convert start_iso (UTC) -> local time
            utc_dt = datetime.fromisoformat(start_iso)
            local_dt = pytz.utc.localize(utc_dt).astimezone(TIMEZONE)
            local_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
            earned = ccount * price
            table.add_row(
                str(sid),
                local_str,
                task,
                format_duration(dur),
                str(ccount),
                f"{price:.2f}",
                f"{earned:.2f}"
            )

        console.print(table)

    def _get_date_range_utc(self, date_str: Optional[str], is_monthly: bool) -> tuple:
        """Get UTC datetime range for daily or monthly summaries."""
        if date_str is None:
            now_local = datetime.now(TIMEZONE)
            date_str = now_local.strftime("%Y-%m" if is_monthly else "%Y-%m-%d")

        if is_monthly:
            year, month = map(int, date_str.split("-"))
            start = TIMEZONE.localize(datetime(year, month, 1))
            if month == 12:
                end = TIMEZONE.localize(datetime(year + 1, 1, 1))
            else:
                end = TIMEZONE.localize(datetime(year, month + 1, 1))
        else:
            start = TIMEZONE.localize(datetime.strptime(date_str, "%Y-%m-%d"))
            end = start + timedelta(days=1)

        return start.astimezone(pytz.utc), end.astimezone(pytz.utc)

    def _generate_summary_table(self, rows, period_str: str, is_monthly: bool) -> None:
        """Generate and display summary table for daily or monthly data."""
        if not rows:
            period_type = "month" if is_monthly else "day"
            console.print(f"[bold magenta]No sessions found for {period_type} {period_str}[/]")
            return

        aggregate = {}
        for (task, dur, cnt, p, st) in rows:
            if task not in aggregate:
                aggregate[task] = {'count': 0, 'duration': 0.0, 'earned': 0.0}
            aggregate[task]['count'] += cnt
            aggregate[task]['duration'] += dur
            aggregate[task]['earned'] += (cnt * p)

        period_type = "Monthly" if is_monthly else "Daily"
        table = Table(title=f"{period_type} Summary for {period_str}", header_style="bold magenta")
        table.add_column("Task", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Earned (€)", justify="right")
        table.add_column("Hourly Rate (€ / hr)", justify="right")

        total_count = total_time = total_earned = 0.0

        for task in sorted(aggregate.keys()):
            data = aggregate[task]
            hours = data['duration'] / 3600 if data['duration'] > 0 else 0
            hourly_rate = data['earned'] / hours if hours > 0 else 0

            table.add_row(
                task,
                str(data['count']),
                format_duration(data['duration']),
                f"{data['earned']:.2f}",
                f"{hourly_rate:.2f}"
            )

            total_count += data['count']
            total_time += data['duration']
            total_earned += data['earned']

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

    def show_daily_summary(self, day_str: Optional[str] = None):
        """
        Show an aggregated daily summary from work_sessions.
        If day_str is None, use today's date in local time.
        Aggregation = sum duration, sum count, sum earned by task.
        """
        start_utc, end_utc = self._get_date_range_utc(day_str, False)
        rows = self.execute("""
            SELECT task_name, duration_seconds, count, price, start_time
            FROM work_sessions
            WHERE start_time >= ? AND start_time < ?
            ORDER BY task_name
        """, (start_utc.isoformat(), end_utc.isoformat())).fetchall()

        self._generate_summary_table(rows, day_str or datetime.now(TIMEZONE).strftime("%Y-%m-%d"), False)

    def parse_stats_arg(self, arg: str) -> tuple[bool, str]:
        import re
        from datetime import datetime, timedelta

        arg_lower = arg.lower().strip()

        # special keywords
        if arg_lower == "yesterday":
            yesterday = datetime.now(TIMEZONE) - timedelta(days=1)
            return (False, yesterday.strftime("%Y-%m-%d"))
        if arg_lower == "today":
            today = datetime.now(TIMEZONE)
            return (False, today.strftime("%Y-%m-%d"))

        # explicit formats
        if re.match(r"^\d{4}-\d{2}-\d{2}$", arg):
            return (False, arg)  # daily
        if re.match(r"^\d{4}-\d{2}$", arg):
            return (True, arg)  # monthly

        # month name? use most recent past occurrence
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12
        }
        if arg_lower in months:
            target_month = months[arg_lower]
            now = datetime.now(TIMEZONE)
            year = now.year if now.month > target_month else now.year - 1
            return (True, f"{year}-{target_month:02d}")

        raise ValueError("invalid date format")

    def show_monthly_summary(self, ym_str: Optional[str] = None):
        """
        Show aggregated monthly summary. If ym_str is None, use current month (local).
        Format of ym_str = YYYY-MM
        """
        start_utc, end_utc = self._get_date_range_utc(ym_str, True)
        rows = self.execute("""
            SELECT task_name, duration_seconds, count, price, start_time
            FROM work_sessions
            WHERE start_time >= ? AND start_time < ?
            ORDER BY task_name
        """, (start_utc.isoformat(), end_utc.isoformat())).fetchall()

        self._generate_summary_table(rows, ym_str or datetime.now(TIMEZONE).strftime("%Y-%m"), True)



    # -------------------------
    # Removal Methods
    # -------------------------

    def remove_session_by_id(self, session_id: int):
        """
        Remove a work session by its ID.
        """
        c = self.execute("SELECT * FROM work_sessions WHERE id = ?;", (session_id,))
        session = c.fetchone()
        if not session:
            console.print(f"[red]No work session found with ID {session_id}.[/]")
            return

        self.execute("DELETE FROM work_sessions WHERE id = ?;", (session_id,))
        console.print(f"[green]Work session with ID {session_id} has been removed.[/]")


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
      List all known tasks, their prices, and last used dates, sorted by last used date.
  [cyan]status[/cyan]
      Show today's daily summary (all tasks).
  [cyan]stats day [YYYY-MM-DD][/cyan]
      Show daily summary for a given day (defaults to today).
  [cyan]stats month [YYYY-MM][/cyan]
      Show monthly summary for a given month (defaults to current month).
  [cyan]history [n][/cyan]
      Show chronological sessions. If n is given, show only the last n sessions.
  [cyan]rm <session_id>[/cyan]
      Remove a work session by its ID.
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
            table = Table(title="Known Tasks (Sorted by Last Used Date)", header_style="bold blue")
            table.add_column("Name", style="cyan")
            table.add_column("Price (€)", justify="right")
            table.add_column("Last Used", justify="right")
            for (tn, pr, last_used) in tasks:
                if last_used:
                    # Convert start_iso (UTC) -> local time
                    utc_dt = datetime.fromisoformat(last_used)
                    local_dt = pytz.utc.localize(utc_dt).astimezone(TIMEZONE)
                    local_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    local_str = "Never"
                table.add_row(tn, f"{pr:.2f}", local_str)
            console.print(table)

        elif cmd == "status":
            # Show today's daily summary
            tracker.show_daily_summary(None)

        elif cmd == "stats":
            if not args:
                tracker.show_daily_summary(None)
                tracker.show_monthly_summary(None)
                continue
            try:
                is_monthly, date_str = tracker.parse_stats_arg(args[0])
            except Exception:
                console.print("[red]Usage: stats [YYYY-MM-DD|YYYY-MM|month|yesterday][/]")
                continue

            if is_monthly:
                tracker.show_monthly_summary(date_str)
            else:
                tracker.show_daily_summary(date_str)

        elif cmd == "history":
            limit = None
            if len(args) == 1:
                try:
                    limit = int(args[0])
                except ValueError:
                    console.print("[red]Invalid limit. Must be an integer.[/]")
            tracker.show_chronological_view(limit)

        elif cmd == "rm":
            if len(args) != 1:
                console.print("[red]Usage: rm <session_id>[/]")
                continue
            target = args[0]
            if target.isdigit():
                # Remove work session by ID
                session_id = int(target)
                tracker.remove_session_by_id(session_id)
            else:
                console.print("[red]Invalid argument. 'rm' command only accepts work session IDs (numbers).[/]")
                continue

        else:
            console.print(f"[red]Unknown command:[/] {cmd} (try 'help')")


if __name__ == "__main__":
    main()
