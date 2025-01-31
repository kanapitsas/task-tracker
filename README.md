# Task Tracker CLI

![License](https://img.shields.io/github/license/kanapitsas/task-tracker-cli)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![SQLite](https://img.shields.io/badge/SQLite-3.34.0%2B-blue)

**Task Tracker CLI** is a lightweight and efficient command-line tool designed for professionals who work on task-based projects with fixed payouts. Whether you're freelancing, managing projects, or tracking repetitive tasks, this CLI provides precise tracking and insightful statistics to help you stay organized and maximize your productivity.

---

## Table of Contents

- [Features](#features)
- [Demo](#demo)
- [Installation](#installation)
- [Getting Started](#getting-started)
  - [Commands](#commands)
- [Database Schema](#database-schema)
- [Technologies Used](#technologies-used)

---

## Features

- **Detailed Session Logging:** Each task session is recorded with start time, duration, and count.
- **Immutable Entries:** Every session is stored as a separate entry for accurate historical tracking.
- **Fixed Payout Management:** Assign fixed payouts to tasks and calculate earnings effortlessly.
- **Real-Time Tracking:** Start, pause, and monitor your tasks in real-time.
- **Comprehensive Statistics:** Generate daily and monthly reports to analyze your performance and earnings.
- **History Tracking:** Easily review your task history and session details.
- **User-Friendly CLI:** Intuitive command-line interface with real-time prompt updates.

---

## Demo

![Task Tracker CLI Demo](./screenshots/demo.gif)

*Watch how Task Tracker CLI helps you manage and track your tasks seamlessly.*

---

## Installation

### Prerequisites

- **Python 3.8 or higher**: Make sure Python is installed on your system. [Download Python](https://www.python.org/downloads/)
- **Git**: To clone the repository. [Download Git](https://git-scm.com/downloads)

### Steps

1. **Clone the Repository**

   ```bash
   git clone https://github.com/yourusername/task-tracker-cli.git
   cd task-tracker-cli
   ```

2. **Create a Virtual Environment (Optional but Recommended)**

   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**

   ```bash
   pip install -r requirements.txt
   ```


4. **Run the Application**

   ```bash
   ./main.py
   ```

   *Or using Python:*

   ```bash
   python main.py
   ```

---

## Getting Started

Once the application is running, you'll interact with it via the command line. Below is a guide to the available commands and how to use them effectively.

### Commands

| Command                      | Description                                                                                               |
|------------------------------|-----------------------------------------------------------------------------------------------------------|
| `help`                       | Displays a help message with all available commands.                                                     |
| `switch <task>`              | Switches the active task to `<task>`. Finalizes the current session if running.                          |
| `start` or `s`               | Starts timing the currently active task.                                                                |
| `pause` or `p`               | Pauses the current session, finalizing it with a count of 0.                                             |
| `<number>`                   | Increments the current task's count by the specified number and finalizes the session with that count.   |
| `<Enter>`                    | Increments the current task's count by 1 and finalizes the session.                                       |
| `set-price <task> <price>`   | Sets or updates the fixed payout of `<task>` to `<price>` euros.                                         |
| `list`                       | Lists all known tasks along with their fixed payouts.                                                    |
| `status`                     | Shows a summary of today's task counts, times, and earnings.                                             |
| `stats`                      | Displays detailed statistics for today and the current month.                                            |
| `stats day <YYYY-MM-DD>`     | Shows statistics for a specific day.                                                                     |
| `stats month <YYYY-MM>`      | Shows statistics for a specific month.                                                                    |
| `history [n]`                | Displays the last `n` task sessions. If `n` is omitted, shows all sessions for today.                    |
| `exit` or `quit`             | Finalizes any running session and exits the application.                                                 |

---

## Database Schema

The application uses SQLite for data storage, ensuring lightweight and efficient data management. Below is an overview of the database schema.

### Tables

1. **`tasks`**

   Stores information about each task.

   | Column  | Type  | Description                         |
   |---------|-------|-------------------------------------|
   | `name`  | TEXT  | Primary key. Name of the task.      |
   | `price` | REAL  | Fixed payout per task in euros.     |

2. **`task_entries`**

   Records each task session with detailed information.

   | Column             | Type    | Description                                                    |
   |--------------------|---------|----------------------------------------------------------------|
   | `id`               | INTEGER | Primary key. Auto-incremented session identifier.             |
   | `task_name`        | TEXT    | Foreign key referencing `tasks(name)`.                        |
   | `start_time`       | TEXT    | ISO8601 formatted UTC timestamp marking session start.        |
   | `duration_seconds` | REAL    | Duration of the session in seconds.                           |
   | `count`            | INTEGER | Number of increments finalized at session end.                 |

---

## Technologies Used

- **Python 3.8+**: The core programming language used for development.
- **SQLite**: Lightweight relational database for storing tasks and session data.
- **Prompt Toolkit**: Enhances the CLI with advanced input features and real-time prompt updates.
- **Rich**: Provides rich text and beautiful formatting in the terminal.
- **Pytz**: Handles timezone conversions and ensures consistent timestamp management.
