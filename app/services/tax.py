"""Apartment tax rates per hour."""
TAX_RATES = {
    "Эконом": 55,
    "Стандарт": 57,
    "Комфорт": 60,
    "Комфорт+": 85,
    "Элитный": 100,
    "Премиум": 95,
    "Люкс": 145,
    "Бизнес": 70,
}


def hourly_tax(class_name: str) -> int:
    return TAX_RATES.get(class_name, 0)


def daily_tax(class_name: str) -> int:
    return hourly_tax(class_name) * 24


def weekly_tax(class_name: str) -> int:
    return hourly_tax(class_name) * 24 * 7


def format_tax(class_name: str) -> str:
    h = hourly_tax(class_name)
    if h == 0:
        return ""
    return f"💵 ${h}/ч · ${daily_tax(class_name)}/сут · ${weekly_tax(class_name)}/7дн"
