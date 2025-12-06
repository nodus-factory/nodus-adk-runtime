"""
Current DateTime Tool

Provides the current date and time to the agent.
This is critical for resolving relative dates like "today", "tomorrow", "next Tuesday", etc.
"""

from datetime import datetime, timezone, timedelta
from google.adk.tools.function_tool import FunctionTool
from typing import Dict, Any
import structlog

logger = structlog.get_logger()


def get_current_datetime(timezone_name: str = "Europe/Madrid") -> Dict[str, Any]:
    """
    Get the current date and time in the specified timezone.
    
    This tool is CRITICAL for resolving relative dates:
    - "today" → use current_date
    - "tomorrow" → current_date + 1 day
    - "next Tuesday" → find next Tuesday from current_date
    - "dimarts 9" → find next Tuesday the 9th from current_date
    
    Args:
        timezone_name: Timezone name (default: "Europe/Madrid")
    
    Returns:
        Dict with:
        - current_date: ISO date string (YYYY-MM-DD)
        - current_time: Time string (HH:MM:SS)
        - current_datetime: Full ISO datetime string with timezone
        - day_of_week: Day name in English (Monday, Tuesday, etc.)
        - day_of_week_catalan: Day name in Catalan (Dilluns, Dimarts, etc.)
        - timezone: Timezone name
        - year: Current year (CRITICAL for resolving dates without year)
    """
    # Get current UTC time
    now_utc = datetime.now(timezone.utc)
    
    # Europe/Madrid is UTC+1 in winter, UTC+2 in summer (CEST)
    # For simplicity, we'll use UTC+1 (winter time)
    # In production, you might want to use pytz or zoneinfo for proper DST handling
    if timezone_name == "Europe/Madrid":
        # Check if we're in DST (rough approximation: March-October)
        is_dst = 3 <= now_utc.month <= 10
        offset_hours = 2 if is_dst else 1
        now_local = now_utc + timedelta(hours=offset_hours)
    else:
        # Default to UTC+1 for other timezones (can be extended)
        now_local = now_utc + timedelta(hours=1)
    
    # Format outputs
    current_date = now_local.strftime("%Y-%m-%d")
    current_time = now_local.strftime("%H:%M:%S")
    current_datetime_iso = now_local.strftime("%Y-%m-%dT%H:%M:%S+01:00")
    day_of_week = now_local.strftime("%A")
    
    # Catalan day names
    day_names_cat = {
        "Monday": "Dilluns",
        "Tuesday": "Dimarts",
        "Wednesday": "Dimecres",
        "Thursday": "Dijous",
        "Friday": "Divendres",
        "Saturday": "Dissabte",
        "Sunday": "Diumenge"
    }
    day_of_week_catalan = day_names_cat.get(day_of_week, day_of_week)
    
    result = {
        "current_date": current_date,
        "current_time": current_time,
        "current_datetime": current_datetime_iso,
        "day_of_week": day_of_week,
        "day_of_week_catalan": day_of_week_catalan,
        "timezone": timezone_name,
        "year": now_local.year,
        "month": now_local.month,
        "day": now_local.day,
    }
    
    logger.info(
        "Current datetime retrieved",
        date=current_date,
        time=current_time,
        day=day_of_week_catalan,
        year=now_local.year
    )
    
    return result


# Create FunctionTool
get_current_datetime_tool = FunctionTool(
    get_current_datetime,
    require_confirmation=False
)


