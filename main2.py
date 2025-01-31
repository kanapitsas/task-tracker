# tracker/main.py
import sqlite3
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from dataclasses import dataclass
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import ANSI, HTML
from rich.console import Console
from rich.table import Table
import pytz

# Configuration
TIMEZONE = pytz.timezone("Europe/Paris")
DB_NAME = "task_tracker.db"
console = Console()

# Database setup
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Tasks table (name, current_price)
    c.execute('''CREATE TABLE IF NOT EXISTS tasks
                 (name TEXT PRIMARY KEY, price REAL)''')

    # Sessions table (task_name, price, start_time, end_time)
    c.execute('''CREATE TABLE IF NOT EXISTS sessions
                 (id INTEGER PRIMARY KEY,
                  task_name TEXT,
                  price REAL,
                  start_time INTEGER,
                  end_time INTEGER)''')
    conn.commit()
    conn.close()

init_db()

@dataclass
class CurrentTask:
    name: str
    price: float
    start_time: datetime
    session_id: int

class TaskTracker:
    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME)
        self.current_task: Optional[CurrentTask] = None
        self.paused = True

    def __del__(self):
        self.conn.close()

    def execute(self, query: str, args=()) -> sqlite3.Cursor:
        c = self.conn.cursor()
        c.execute(query, args)
        self.conn.commit()
        return c

    # Task management
    def get_task_price(self, task_name: str) -> Optional[float]:
        c = self.execute("SELECT price FROM tasks WHERE name=?", (task_name,))
        return c.fetchone()[0] if c.fetchone() else None

    def update_task_price(self, task_name: str, new_price: float):
        self.execute(
            "INSERT OR REPLACE INTO tasks (name, price) VALUES (?, ?)",
            (task_name, new_price)
        )

    def list_tasks(self) -> List[Tuple[str, float]]:
        c = self.execute("SELECT name, price FROM tasks ORDER BY name")
        return c.fetchall()

    # Session tracking
    def start_task(self, task_name: str):
        if self.current_task:
            self.stop_task()

        price = self.get_task_price(task_name)
        if not price:
            raise ValueError(f"Task {task_name} not found. Set price first.")

        start_time = datetime.now(TIMEZONE)
        c = self.execute(
            "INSERT INTO sessions (task_name, price, start_time) VALUES (?, ?, ?)",
            (task_name, price, start_time.timestamp())
        )
        self.current_task = CurrentTask(
            name=task_name,
            price=price,
            start_time=start_time,
            session_id=c.lastrowid
        )
        self.paused = False

    def stop_task(self):
        if self.current_task:
            end_time = datetime.now(TIMEZONE)
            self.execute(
                "UPDATE sessions SET end_time=? WHERE id=?",
                (end_time.timestamp(), self.current_task.session_id)
            )
            self.current_task = None
            self.paused = True

    # Statistics
    def get_stats(self, period: str = "day") -> dict:
        now = datetime.now(TIMEZONE)

        if period == "day":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            raise ValueError("Invalid period. Use 'day' or 'month'")

        query = """
            SELECT task_name, SUM(price), SUM(end_time - start_time)
            FROM sessions
            WHERE start_time >= ?
            AND end_time IS NOT NULL
            GROUP BY task_name
        """
        c = self.execute(query, (start.timestamp(),))
        results = c.fetchall()

        total_earned = sum(price for _, price, _ in results)
        total_seconds = sum(duration for _, _, duration in results)

        return {
            "tasks": results,
            "total_earned": total_earned,
            "total_time": timedelta(seconds=total_seconds),
            "hourly_rate": total_earned / (total_seconds / 3600) if total_seconds else 0
        }

    def get_current_status(self) -> dict:
        if not self.current_task or self.paused:
            return {"active": False}

        now = datetime.now(TIMEZONE)
        duration = now - self.current_task.start_time
        return {
            "active": True,
            "task_name": self.current_task.name,
            "duration": duration,
            "price": self.current_task.price
        }

def format_timedelta(delta: timedelta) -> str:
    hours, remainder = divmod(delta.total_seconds(), 3600)
    minutes = remainder // 60
    return f"{int(hours):02d}:{int(minutes):02d}"

def display_stats(period: str, tracker: TaskTracker):
    stats = tracker.get_stats(period)

    table = Table(title=f"{period.capitalize()} Statistics", show_header=True, header_style="bold magenta")
    table.add_column("Task", style="cyan")
    table.add_column("Earnings", justify="right")
    table.add_column("Time Spent", justify="right")

    for task, earnings, seconds in stats["tasks"]:
        table.add_row(
            task,
            f"€{earnings:.2f}",
            format_timedelta(timedelta(seconds=seconds))
        )

    table.add_row("Total",
                  f"€{stats['total_earned']:.2f}",
                  format_timedelta(stats['total_time']))

    console.print(table)
    console.print(f"Hourly Rate: [bold]€{stats['hourly_rate']:.2f}/h[/bold]")

def main():
    tracker = TaskTracker()
    prompt_session = PromptSession()
    style = Style.from_dict({
        'prompt': 'ansicyan bold',
    })

# Updated section in the main() function
def main():
    tracker = TaskTracker()
    prompt_session = PromptSession()
    style = Style.from_dict({
        'prompt': 'ansicyan bold',
    })

    while True:
        try:
            # Build dynamic prompt
            status = tracker.get_current_status()
            prompt_str = ""

            if status["active"]:
                prompt_str = HTML(
                    f"<b>[<style fg='green'>●</style> {status['task_name']} "
                    f"{format_timedelta(status['duration'])}]</b> ➜ "
                )
            else:
                prompt_str = HTML("<b>[<style fg='red'>■</style> paused]</b> ➜ ")

            command = prompt_session.prompt(
                prompt_str,  # Pass the single HTML object directly
                style=style,
                completer=None,
                complete_while_typing=False
            ).strip().split()

            # ... rest of the code remains the same ...

            if not command:
                continue

            cmd = command[0].lower()
            args = command[1:]

            if cmd in ("start", "s"):
                if len(args) < 1:
                    console.print("[red]Error:[/] Please specify a task name")
                    continue
                tracker.start_task(args[0])
                console.print(f"Started [green]{args[0]}[/] at {datetime.now(TIMEZONE).strftime('%H:%M')}")

            elif cmd in ("stop", "x"):
                tracker.stop_task()
                console.print("[yellow]Current task stopped[/]")

            elif cmd == "tasks":
                tasks = tracker.list_tasks()
                table = Table(title="Available Tasks", show_header=True, header_style="bold blue")
                table.add_column("Name", style="cyan")
                table.add_column("Price", justify="right")
                for name, price in tasks:
                    table.add_row(name, f"€{price:.2f}")
                console.print(table)

            elif cmd == "stats":
                period = args[0] if args else "day"
                display_stats(period, tracker)

            elif cmd == "set-price":
                if len(args) != 2:
                    console.print("[red]Usage:[/] set-price <task> <new_price>")
                    continue
                try:
                    tracker.update_task_price(args[0], float(args[1]))
                    console.print(f"Updated [green]{args[0]}[/] price to €{float(args[1]):.2f}")
                except ValueError:
                    console.print("[red]Error:[/] Invalid price format")

            elif cmd in ("exit", "quit"):
                if tracker.get_current_status()["active"]:
                    tracker.stop_task()
                console.print("Goodbye!")
                break

            else:
                console.print(f"[red]Unknown command:[/] {cmd}")

        except KeyboardInterrupt:
            console.print("\nUse 'exit' to quit")
        except Exception as e:
            console.print(f"[red]Error:[/] {str(e)}")

if __name__ == "__main__":
    main()
