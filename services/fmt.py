# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 RYXLAB SOFT TECH SRL (RyxLabs)
# SPDX-License-Identifier: GPL-2.0-or-later
"""Romanian number and count formatting for messages ("12.345,5", "20 de imobile")."""


def number(value, decimals=2, thousands=True) -> str:
    """"12.345,67", trailing zero decimals dropped: 12000.0 -> "12.000", 2.5 -> "2,5"."""
    if value is None:
        return ""
    text = f"{value:,.{decimals}f}" if thousands else f"{value:.{decimals}f}"
    if decimals:
        text = text.rstrip("0").rstrip(".")
    # swap the English separators through a placeholder
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def area(value, decimals=2) -> str:
    return f"{number(value, decimals)} mp"


def percent(value, decimals=2) -> str:
    return f"{number(value, decimals, thousands=False)}%"


def plural(n: int, one: str, many: str) -> str:
    """one or many by count; Romanian verbs agree with the count, not only the noun."""
    return one if n == 1 else many


def count(n: int, singular: str, plural: str) -> str:
    """"1 imobil", "5 imobile", "20 de imobile", "101 imobile": Romanian adds "de" when the
    last two digits are 00 or 20 to 99."""
    if n == 1:
        return f"1 {singular}"
    tail = n % 100
    if n != 0 and (tail == 0 or tail >= 20):
        return f"{number(n, 0)} de {plural}"
    return f"{number(n, 0)} {plural}"
