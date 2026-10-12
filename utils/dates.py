from datetime import date, timedelta


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())