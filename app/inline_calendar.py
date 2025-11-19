
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
import calendar as cal

def month_kb(year:int, month:int):
    kb = InlineKeyboardMarkup(row_width=7)
    kb.row(
        InlineKeyboardButton("«", callback_data=f"cal_nav:{year}:{month}:-1"),
        InlineKeyboardButton(f"{year}-{str(month).zfill(2)}", callback_data="noop"),
        InlineKeyboardButton("»", callback_data=f"cal_nav:{year}:{month}:1"),
    )
    week = ["Mo","Tu","We","Th","Fr","Sa","Su"]
    kb.row(*[InlineKeyboardButton(w, callback_data="noop") for w in week])
    month_cal = cal.monthcalendar(year, month)
    for week in month_cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="noop"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"cal_pick:{year}:{month}:{day}"))
        kb.row(*row)
    today = datetime.now().date()
    kb.row(
        InlineKeyboardButton("Сегодня", callback_data=f"cal_pick:{today.year}:{today.month}:{today.day}"),
        InlineKeyboardButton("Завтра", callback_data=f"cal_pick:{(today+timedelta(days=1)).year}:{(today+timedelta(days=1)).month}:{(today+timedelta(days=1)).day}")
    )
    return kb
