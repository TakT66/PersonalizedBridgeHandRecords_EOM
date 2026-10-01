#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bridge Board Image Extractor — Streamlit Web Edition
Μεταφορά της desktop εφαρμογής σε web app.
"""

import io
import re
import sys
import math
import pandas as pd
import tempfile
import threading
from datetime import date, timedelta, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FONT_PATH = BASE_DIR / "DejaVuSans.ttf"
FONT_BOLD_PATH = BASE_DIR / "DejaVuSans-Bold.ttf"
FONT_BOLD_ITALIC_PATH = BASE_DIR / "DejaVuSans-BoldOblique.ttf"

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

# ── endplay import (DDS) ────────────────────────────────────────────────────
try:
    from endplay.types import Deal, Denom, Player
    from endplay.dds import calc_dd_table, analyse_play
    DDS_AVAILABLE = True
except Exception:
    DDS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants (identical to desktop version)
# ---------------------------------------------------------------------------
COLS            = 3
ROWS            = 5   # χρησιμοποιείται μόνο για τον υπολογισμό του μεγέθους κάθε board
ROWS_PER_PAGE   = 4   # πόσες σειρές χωράνε πραγματικά στη σελίδα χωρίς κόψιμο
BOARDS_PER_PAGE = COLS * ROWS_PER_PAGE
MARGIN          = 40
PADDING         = 16
ROW_GAP         = 23  # κατακόρυφο κενό μεταξύ σειρών (~Arial 11pt @ 150dpi)
A4_W, A4_H      = 1240, 1754

SUIT_ORDER  = ["S", "H", "D", "C"]
SUIT_SYMBOL = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
SUIT_COLOR  = {
    "S": (0,   0,   0),
    "H": (180, 30,  30),
    "D": (160, 80,  0),
    "C": (0,   110, 0),
}
HCP_VALUE = {"A": 4, "K": 3, "Q": 2, "J": 1}
DENOM_MAP = {"NT": "nt", "S": "spades", "H": "hearts", "D": "diamonds", "C": "clubs"}
PLAYER_MAP = {"N": "north", "S": "south", "E": "east", "W": "west"}

# Χάρτες με πραγματικά αντικείμενα endplay (για την ανάλυση του αντάμ)
if DDS_AVAILABLE:
    LEAD_DENOM_MAP = {
        "NT": Denom.nt, "S": Denom.spades, "H": Denom.hearts,
        "D": Denom.diamonds, "C": Denom.clubs,
    }
    LEAD_PLAYER_MAP = {
        "N": Player.north, "S": Player.south,
        "E": Player.east,  "W": Player.west,
    }


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------
#VALID_FILE = Path(__file__).resolve().parent / "Valid_Registration_Numbers.txt"
#
#def load_valid_numbers():
#    if not VALID_FILE.exists():
#        return set()
#    with open(VALID_FILE, encoding="utf-8", errors="ignore") as f:
#        return {line.strip() for line in f if line.strip()}

def load_valid_numbers():
    try:
        # Διαβάζει τη λίστα απευθείας από τα Secrets του Streamlit
        # Επιστρέφει ένα set για πολύ γρήγορη αναζήτηση
        return set(st.secrets["ALLOWED_AM"])
    except Exception:
        # Αν ξεχάσεις να τα ορίσεις στα Secrets, επιστρέφει κενό σετ
        st.error("Critical Error: 'ALLOWED_AM' not found in Secrets.")
        return set()


# ---------------------------------------------------------------------------
# Download Logger (Google Sheets) — αποτυχία δεν επηρεάζει την εφαρμογή
# ---------------------------------------------------------------------------
SHEET_NAME = "Downloads"  # Το όνομα του Google Sheet που έφτιαξες

def log_download(reg, tournament, club, filename):
    """
    Γράφει μια γραμμή στο Google Sheet κάθε φορά που κατεβαίνει PDF.
    Αν αποτύχει για οποιονδήποτε λόγο, αγνοεί σιωπηλά το σφάλμα
    ώστε η εφαρμογή να συνεχίζει κανονικά.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc    = gspread.authorize(creds)
        sh    = gc.open(SHEET_NAME)
        ws    = sh.sheet1

        from datetime import timezone, timedelta
        tz_greece = timezone(timedelta(hours=3))
        timestamp = datetime.now(tz_greece).strftime("%Y-%m-%d %H:%M:%S")
        ws.append_row(
            [timestamp, reg, tournament, club, filename],
            value_input_option="USER_ENTERED"
        )
    except Exception:
        pass  # Σιωπηλή αποτυχία — η εφαρμογή συνεχίζει κανονικά


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

def make_font(size):
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except Exception:
        return ImageFont.load_default()

def make_bold_font(size):
    try:
        return ImageFont.truetype(str(FONT_BOLD_PATH), size)
    except Exception:
        return make_font(size)


def make_bold_italic_font(size):
    try:
        return ImageFont.truetype(str(FONT_BOLD_ITALIC_PATH), size)
    except Exception:
        return make_bold_font(size)

_MI  = Image.new("RGB", (4, 4))
_MD  = ImageDraw.Draw(_MI)

def tw(text, font):
    try:
        b = _MD.textbbox((0, 0), text, font=font)
        return b[2] - b[0]
    except Exception:
        return max(1, len(text)) * max(6, font.size // 2)

def th(font):
    try:
        b = _MD.textbbox((0, 0), "Ag", font=font)
        return b[3] - b[1]
    except Exception:
        return font.size + 2

# ---------------------------------------------------------------------------
# Hand / HCP / KRHCP helpers (unchanged from desktop)
# ---------------------------------------------------------------------------
def parse_hand(hand_str):
    parts = hand_str.split(".")
    if len(parts) != 4:
        return {s: "" for s in SUIT_ORDER}
    return {suit: ranks for suit, ranks in zip(SUIT_ORDER, parts)}

def calc_hcp(hand_str):
    return sum(HCP_VALUE.get(ch.upper(), 0) for ch in hand_str)

def calc_krhcp(hand_str):
    hand = parse_hand(hand_str)
    RANKS = "AKQJT98765432"
    def rank_idx(r):
        return RANKS.index(r) if r in RANKS else 12
    def suit_krhcp(ranks_str):
        if not ranks_str or ranks_str == "-":
            length, cards = 0, []
        else:
            length = len(ranks_str)
            cards  = list(ranks_str.upper())
        has      = lambda r: r in cards
        n_higher = lambda r: sum(1 for c in cards if rank_idx(c) < rank_idx(r))
        pts = 0.0
        if has("A"):  pts += 4
        if has("K"):  pts += 3
        if has("Q"):  pts += 2
        if has("J"):  pts += 1
        if has("T"):  pts += 0.5
        if 2 <= length <= 6 and has("T"):
            if has("J") or n_higher("T") >= 2:
                pts += 0.5
        if 2 <= length <= 6 and has("9"):
            if has("8") or has("T") or n_higher("9") == 2:
                pts += 0.5
        if 4 <= length <= 6 and has("9") and not has("8") and not has("T"):
            if n_higher("9") == 3:
                pts += 0.5
        if length >= 7 and (not has("Q") or not has("J")):
            pts += 1
        if length >= 8 and not has("Q"):
            pts += 1
        if length >= 9 and not has("Q") and not has("J"):
            pts += 1
        pts = pts * length / 10
        if has("A"):  pts += 3
        if has("K") and length >= 2: pts += 2
        if has("K") and length == 1: pts += 0.5
        if has("Q") and length >= 3 and (has("A") or has("K")): pts += 1
        if has("Q") and length >= 3 and not has("A") and not has("K"): pts += 0.75
        if has("Q") and length == 2 and (has("A") or has("K")): pts += 0.5
        if has("Q") and length == 2 and not has("A") and not has("K"): pts += 0.25
        if has("J") and n_higher("J") == 2: pts += 0.5
        if has("J") and n_higher("J") == 1: pts += 0.25
        if has("T") and n_higher("T") == 2: pts += 0.25
        if has("T") and has("9") and n_higher("T") == 1: pts += 0.25
        if length == 0: pts += 3
        if length == 1: pts += 2
        if length == 2: pts += 1
        return pts
    suit_totals = [suit_krhcp(hand[s]) for s in SUIT_ORDER]
    total = sum(suit_totals) - 1
    lengths = sorted([len(hand[s]) if hand[s] and hand[s] != "-" else 0
                      for s in SUIT_ORDER], reverse=True)
    if lengths == [4, 3, 3, 3]:
        total += 0.5
    return round(total, 1)

# ---------------------------------------------------------------------------
# DDS
# ---------------------------------------------------------------------------
def board_to_deal(board):
    pbn = "N:{} {} {} {}".format(
        board["north"], board["east"], board["south"], board["west"])
    return Deal(pbn)

def run_dds(board):
    if not DDS_AVAILABLE:
        return None
    try:
        deal  = board_to_deal(board)
        table = calc_dd_table(deal)
        result = {pl: {} for pl in PLAYER_MAP}
        txt = str(table)
        sym_map = {"♣":"C","♦":"D","♥":"H","♠":"S","NT":"NT",
                   "C":"C","D":"D","H":"H","S":"S"}
        segments = txt.strip().split(";")
        dn_order = [sym_map.get(s.strip()) for s in segments[0].split(",")]
        dn_order = [d for d in dn_order if d]
        for seg in segments[1:]:
            if ":" not in seg:
                continue
            pl, vals_str = seg.split(":", 1)
            pl = pl.strip()
            if pl not in result:
                continue
            vals = vals_str.split(",")
            for i, dn in enumerate(dn_order):
                if i < len(vals):
                    try:
                        result[pl][dn] = int(vals[i].strip())
                    except ValueError:
                        result[pl][dn] = 0
        for pl in result:
            for dn in DENOM_MAP:
                result[pl].setdefault(dn, 0)
        has_data = any(result[p][d] > 0 for p in result for d in result[p])
        return result if has_data else None
    except Exception:
        return None

def optimum_contract(dds_table, vul):
    if not dds_table:
        return None
    ns_vul = vul in ("NS", "All")
    ew_vul = vul in ("EW", "All")
    def contract_score(level, denom, side, tricks_made):
        is_vul = ns_vul if side == "NS" else ew_vul
        if denom == "NT":     trick_score = 40 + 30 * (level - 1)
        elif denom in ("S","H"): trick_score = 30 * level
        else:                 trick_score = 20 * level
        overtricks = tricks_made - (6 + level)
        if overtricks < 0:
            return overtricks * (100 if is_vul else 50)
        score = trick_score
        if trick_score >= 100: score += 500 if is_vul else 300
        else: score += 50
        if level == 6: score += 750 if is_vul else 500
        elif level == 7: score += 1500 if is_vul else 1000
        ot_val = 30 if denom in ("NT","S","H") else 20
        score += overtricks * ot_val
        return score
    denom_disp = {"NT":"NT","S":"♠","H":"♥","D":"♦","C":"♣"}
    best_score, best_side, best_str, best_val = -9999, "NS", "PASS", 0
    for level in range(1, 8):
        for dn in ["NT","S","H","D","C"]:
            for side, players in [("NS",["N","S"]),("EW",["E","W"])]:
                best_tricks = max(dds_table.get(p,{}).get(dn,0) for p in players)
                sc = contract_score(level, dn, side, best_tricks)
                if sc > best_score:
                    best_score = sc; best_side = side
                    best_str = str(level) + denom_disp[dn]; best_val = sc
    sign = "+" if best_val >= 0 else ""
    return (best_side, best_str, sign + str(best_val))

# ---------------------------------------------------------------------------
# Opening-lead analysis (αντάμ)
# ---------------------------------------------------------------------------
def normalize_declarer(value):
    """
    Normalize the declarer's direction.
    The results page uses Greek compass initials:
        Β = Βορράς = N
        Α = Ανατολή = E
        Ν = Νότος   = S
        Δ = Δύση    = W
    English/PBN N/E/S/W are also accepted.
    """
    if value is None:
        return None
    s = str(value).strip().upper()
    if not s:
        return None

    greek = {
        "Β": "N", "ΒΟΡΡΑΣ": "N",
        "Α": "E", "ΑΝΑΤΟΛΗ": "E",
        "Ν": "S", "ΝΟΤΟΣ": "S",
        "Δ": "W", "ΔΥΣΗ": "W",
    }
    if s in greek:
        return greek[s]

    if s[0] in "NESW":
        return s[0]

    english = {"NORTH": "N", "EAST": "E", "SOUTH": "S", "WEST": "W"}
    return english.get(s)


def normalize_contract(contract):
    """
    Extract (level, denomination) from the actual played contract.
    Examples:  2♥ +1 -> (2, H),  4ΧΑ+3 -> (4, NT),  3♠ *-2 -> (3, S)
    The result (+1/-2), doubling etc. are ignored.
    """
    if not contract:
        return None, None

    s = str(contract).strip().upper()

    m = re.search(r'([1-7])\s*(NT|ΧΑ|N|S|H|D|C|♠|♥|♦|♣)', s)
    if not m:
        return None, None

    level = int(m.group(1))
    raw = m.group(2)

    denom = {
        "NT": "NT", "ΧΑ": "NT", "N": "NT",
        "S": "S", "♠": "S",
        "H": "H", "♥": "H",
        "D": "D", "♦": "D",
        "C": "C", "♣": "C",
    }[raw]

    return level, denom


def opening_leader(declarer):
    """Opening leader is the player immediately to declarer's left."""
    d = normalize_declarer(declarer)
    # Clockwise order N -> E -> S -> W; the player on declarer's left is the next one.
    return {"N": "E", "E": "S", "S": "W", "W": "N"}.get(d)


def analyse_opening_leads(board, contract, declarer_raw):
    """
    Evaluate all 13 possible opening leads of the actual opening leader.

    Returns (leader, colours) where colours maps a card ("S2", "HK", ...) to
        green = contract makes with an overtrick (+)
        black = contract makes exactly (=)
        red   = contract goes down (-)

    On any problem returns (None, {}) / leaves the affected card black,
    so the rest of the PDF is never affected.
    """
    if not DDS_AVAILABLE:
        return None, {}

    level, denom = normalize_contract(contract)
    declarer = normalize_declarer(declarer_raw)
    leader = opening_leader(declarer)

    if level is None or denom is None or leader is None:
        return None, {}

    target = 6 + level

    seat_key = {"N": "north", "E": "east", "S": "south", "W": "west"}[leader]
    hand = parse_hand(board[seat_key])
    cards = []
    for suit in SUIT_ORDER:
        for rank in hand[suit]:
            if rank and rank != "-":
                cards.append(suit + rank)

    if len(cards) != 13:
        return leader, {}

    colours = {}
    for card in cards:
        try:
            deal = board_to_deal(board)
            deal.first = LEAD_PLAYER_MAP[leader]
            deal.trump = LEAD_DENOM_MAP[denom]

            # analyse_play returns declarer's double-dummy tricks before the
            # play and after the supplied card.
            analysis = analyse_play(deal, [card])
            if len(analysis) < 2:
                raise RuntimeError("unexpected analyse_play result")

            tricks_after_lead = int(analysis[1])

            if tricks_after_lead > target:
                colours[card] = (0, 150, 0)       # green  (+)
            elif tricks_after_lead == target:
                colours[card] = (0, 0, 0)         # black  (=)
            else:
                colours[card] = (210, 0, 0)       # red    (-)
        except Exception:
            colours[card] = (0, 0, 0)             # keep original look

    return leader, colours

# ---------------------------------------------------------------------------
# PBN parser
# ---------------------------------------------------------------------------
def parse_pbn(pbn_text):
    text = pbn_text
    deal_positions = [(m.start(), m.group(1))
                      for m in re.finditer(r'\[Deal\s+"([^"]+)"', text, re.IGNORECASE)]
    if not deal_positions:
        return []
    def tag_in(chunk, *names):
        for name in names:
            m = re.search(r'\[' + name + r'\s+"([^"]*)"', chunk, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None
    vul_map = {
        "NONE":"None","-":"None","0":"None",
        "NS":"NS","N-S":"NS","N/S":"NS",
        "EW":"EW","E-W":"EW","E/W":"EW",
        "ALL":"All","BOTH":"All","B":"All","BO":"All",
    }
    block_boundaries = []
    for idx, (pos, _) in enumerate(deal_positions):
        blk_start = 0 if idx == 0 else deal_positions[idx-1][0]
        blk_end   = (deal_positions[idx+1][0] if idx+1 < len(deal_positions) else len(text))
        block_boundaries.append((blk_start, blk_end))
    boards = []
    for idx, (pos, deal_str) in enumerate(deal_positions):
        blk_start, blk_end = block_boundaries[idx]
        chunk = text[blk_start:blk_end]
        prefix = deal_str.upper()
        if prefix.startswith("N:"):
            hands_str = deal_str[2:]
            parts = hands_str.split()
        else:
            continue
        if len(parts) < 4:
            continue
        north, east, south, west = parts[0], parts[1], parts[2], parts[3]
        board_num_str = tag_in(chunk, "Board", "BoardNum", "BordNr")
        try:
            board_num = int(board_num_str) if board_num_str else idx + 1
        except ValueError:
            board_num = idx + 1
        vul_raw = (tag_in(chunk, "Vulnerable", "Vul") or "None").upper().strip()
        vul     = vul_map.get(vul_raw, "None")
        dealer  = (tag_in(chunk, "Dealer") or "N").upper().strip()
        boards.append({
            "board": board_num, "north": north, "east": east,
            "south": south, "west": west, "vul": vul, "dealer": dealer,
        })
    return boards

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def fetch_pbn_from_url(page_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    candidate = page_url.rstrip("/") + "/pbn"
    try:
        r = requests.get(candidate, headers=headers, timeout=20)
        if r.ok and len(r.content) > 100:
            for enc in ("utf-8", "windows-1253", "iso-8859-7"):
                try:
                    return r.content.decode(enc)
                except Exception:
                    pass
            return r.text
    except Exception:
        pass
    # Fallback: scan page for PBN link
    try:
        r = requests.get(page_url, headers=headers, timeout=20)
        r.raise_for_status()
        for link in re.findall(r'href=["\']([^"\']+)["\']', r.text, re.IGNORECASE):
            if "pbn" in link.lower():
                pbn_url = (link if link.startswith("http")
                           else page_url.rstrip("/") + "/" + link.lstrip("/"))
                r2 = requests.get(pbn_url, headers=headers, timeout=20)
                r2.raise_for_status()
                for enc in ("utf-8", "windows-1253", "iso-8859-7"):
                    try:
                        return r2.content.decode(enc)
                    except Exception:
                        pass
                return r2.text
    except Exception:
        pass
    return None

def scrape_tournament_info(page_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(page_url, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception:
        return ""
    for enc in ("utf-8", "windows-1253", "iso-8859-7"):
        try:
            text = r.content.decode(enc); break
        except Exception:
            text = r.text
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"&nbsp;", " ", plain)
    plain = re.sub(r"&amp;",  "&", plain)
    plain = re.sub(r"&#[0-9]+;", "", plain)
    plain = re.sub(r"[ \t]+", " ", plain).replace("\xa0", " ")
    date_str = ""
    m = re.search(r"Ημερομηνία\s*([0-9]{1,2}[/\-\.][0-9]{1,2}[/\-\.][0-9]{2,4})", plain)
    if m:
        date_str = m.group(1).strip()
    else:
        m = re.search(r"([0-9]{2}/[0-9]{2}/[0-9]{4})", plain)
        if m:
            date_str = m.group(1)
    name_str = ""
    m = re.search(r"<title[^>]*>([^<]+)</title>", text, re.IGNORECASE)
    if m:
        name_str = m.group(1).strip()
    parts  = [p for p in [name_str, date_str] if p]
    return "  |  ".join(parts)

def find_card_url(page_url, reg_number):
    headers_http = {"User-Agent": "Mozilla/5.0"}
    for enc in ("utf-8", "windows-1253", "iso-8859-7"):
        try:
            r = requests.get(page_url, headers=headers_http, timeout=20)
            text = r.content.decode(enc); break
        except Exception:
            text = r.text
    pattern = (r'aria-label="[^"]*' + re.escape(str(reg_number)) +
               r'[^"]*"\s+href="([^"]+/card/\d+)"')
    m = re.search(pattern, text)
    if m:
        href = m.group(1)
        if href.startswith("http"):
            return href
        base = re.match(r'(https?://[^/]+)', page_url)
        return (base.group(1) if base else "") + href
    idx = text.find(str(reg_number))
    if idx >= 0:
        snippet = text[max(0, idx-300):idx+300]
        m2 = re.search(r'href="(/results/[^"]+/card/\d+)"', snippet)
        if m2:
            base = re.match(r'(https?://[^/]+)', page_url)
            return (base.group(1) if base else "") + m2.group(1)
    return None

def scrape_pair_results(card_url, page_url):
    suit_map = {"icon-spade":"♠","icon-heart":"♥","icon-diamond":"♦","icon-club":"♣"}
    headers_http = {"User-Agent": "Mozilla/5.0"}
    if not card_url.startswith("http"):
        base = re.match(r'(https?://[^/]+)', page_url)
        card_url = (base.group(1) if base else "") + card_url
    for enc in ("utf-8", "windows-1253", "iso-8859-7"):
        try:
            r = requests.get(card_url, headers=headers_http, timeout=20)
            text = r.content.decode(enc); break
        except Exception:
            text = r.text
    for cls, sym in suit_map.items():
        text = re.sub(r'<i[^>]*class="[^"]*' + cls + r'[^"]*"[^>]*>', sym, text)
        text = re.sub(r'<i[^>]*' + cls + r'[^>]*>', sym, text)
    tbl_m = re.search(r'<table[^>]*class="[^"]*results card pairs[^"]*"[^>]*>',
                      text, re.IGNORECASE)
    if not tbl_m:
        return {}
    tbl_start = tbl_m.start()
    tbl_end   = text.find('</table>', tbl_start)
    tbl       = text[tbl_start:tbl_end+8] if tbl_end >= 0 else text[tbl_start:]
    rows      = re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.DOTALL | re.IGNORECASE)
    def cell_text(td_html):
        s = re.sub(r'<[^>]+>', ' ', td_html)
        s = re.sub(r'&nbsp;', ' ', s)
        s = re.sub(r'&amp;', '&', s)
        s = re.sub(r'&#[0-9]+;', '', s)
        return re.sub(r'\s+', ' ', s).strip()
    results = {}
    for row in rows:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
        if len(tds) < 5:
            continue
        try:
            board_num, bnum_idx = None, None
            for _bi, _td in enumerate(tds):
                if _bi < 2: continue
                _v = cell_text(_td).strip()
                if _v.isdigit() and 1 <= int(_v) <= 99:
                    board_num = int(_v); bnum_idx = _bi; break
            if board_num is None:
                continue
            bi = bnum_idx
            def first_a_text(td_html):
                m = re.search(r'<a[^>]*>(.*?)</a>', td_html, re.DOTALL)
                return cell_text(m.group(1)) if m else cell_text(td_html)
            # Round is two columns before board number (bi-2), table is bi-1
            round_val = cell_text(tds[bi-2]) if bi >= 2 else ""
            table_val = cell_text(tds[bi-1]) if bi >= 1 else ""
            opp1     = first_a_text(tds[bi+2]) if len(tds) > bi+2 else ""
            opp2     = first_a_text(tds[bi+4]) if len(tds) > bi+4 else ""
            contract = cell_text(tds[bi+5]) if len(tds) > bi+5 else ""
            declarer = cell_text(tds[bi+6]) if len(tds) > bi+6 else ""
            lead     = cell_text(tds[bi+7]) if len(tds) > bi+7 else ""
            score    = cell_text(tds[bi+8]) if len(tds) > bi+8 else ""
            pct      = cell_text(tds[bi+9]) if len(tds) > bi+9 else ""
            if board_num not in results:
                results[board_num] = {
                    "round": round_val, "table": table_val,
                    "opponent1": opp1, "opponent2": opp2,
                    "contract": contract, "declarer": declarer,
                    "lead": lead, "score": score, "pct": pct,
                }
        except Exception:
            continue
    return results

# ---------------------------------------------------------------------------
# Tournament scraper
# ---------------------------------------------------------------------------
def _clean_html(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'&nbsp;', ' ', s)
    s = re.sub(r'&amp;', '&', s)
    s = re.sub(r'&#[0-9]+;', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def _decode(r):
    for enc in ("utf-8", "windows-1253", "iso-8859-7"):
        try:
            return r.content.decode(enc)
        except Exception:
            pass
    return r.text

def scrape_tournament_list(max_page=8):
    base    = "https://hellasbridge.org/results"
    headers = {"User-Agent": "Mozilla/5.0"}
    tournaments = []
    seen_urls   = set()
    for page in range(1, max_page + 1):
        url = base if page == 1 else "{}?page={}".format(base, page)
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
        except Exception:
            continue
        text = _decode(r)
        row_blocks  = re.findall(r'<tr[^>]*>.*?</tr>', text, re.DOTALL | re.IGNORECASE)
        row_blocks += re.findall(r'<li[^>]*>.*?</li>', text, re.DOTALL | re.IGNORECASE)
        for block in row_blocks:
            m_link = re.search(
                r'href=["\'](/results/(\d+)(?:[^"\']*)?)["\'][^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE)
            if not m_link:
                continue
            tid, raw_title = m_link.group(2), m_link.group(3)
            base_url = "https://hellasbridge.org/results/" + tid
            if base_url in seen_urls:
                continue
            seen_urls.add(base_url)
            title = _clean_html(raw_title)
            if not title:
                continue
            date_str = ""
            m_date = re.search(
                r'(\b\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}\b'
                r'|\b\d{4}[/\-\.]\d{2}[/\-\.]\d{2}\b)',
                _clean_html(block))
            if m_date:
                date_str = m_date.group(1)
            club = ""
            tds = re.findall(r'<td[^>]*>(.*?)</td>', block, re.DOTALL | re.IGNORECASE)
            for td in tds:
                candidate = _clean_html(td)
                if (not candidate or candidate == title
                        or re.fullmatch(r'[\d/\-\. ]+', candidate)
                        or len(candidate) < 3):
                    continue
                club = candidate; break
            tournaments.append({"title": title, "url": base_url,
                                 "date": date_str, "club": club})
    return tournaments

def parse_date_str(date_str):
    if not date_str:
        return None
    for fmt in ("%d/%m/%Y","%d-%m-%Y","%d.%m.%Y",
                "%Y/%m/%d","%Y-%m-%d","%Y.%m.%d",
                "%d/%m/%y","%d-%m-%y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            pass
    return None

# ---------------------------------------------------------------------------
# Rendering (identical logic to desktop)
# ---------------------------------------------------------------------------
def render_board(board, cell_w, cell_h, pair_results=None, header=""):
    dds_table = run_dds(board)
    opt       = optimum_contract(dds_table, board.get("vul","None")) if dds_table else None

    caption = ""
    lead_colours = {}
    pr = pair_results.get(board["board"]) if pair_results else None
    if pr:
        table_parts = pr.get("table", "").split()
        orientation = table_parts[-1] if table_parts else pr.get("table", "")
        round_str = ("Γύρος " + pr["round"]) if pr.get("round") else ""
        opp = " - ".join(filter(None, [pr.get("opponent1",""), pr.get("opponent2","")]))
        first_parts = [p for p in [round_str, orientation, opp] if p]
        parts2 = ["  ".join(first_parts)] if first_parts else []
        detail = []
        if pr.get("contract"): detail.append(pr["contract"])
        if pr.get("declarer"): detail.append("από " + pr["declarer"])
        if pr.get("lead"):     detail.append("Lead: " + pr["lead"])
        if pr.get("score"):    detail.append(pr["score"])
        if pr.get("pct"):      detail.append(pr["pct"].rstrip("%") + "%")
        if detail:
            parts2.append("  ".join(detail))
        caption = "\n".join(parts2)

        # Χρωματισμός των 13 φύλλων του αντάμ (πράσινο +, μαύρο =, κόκκινο -)
        try:
            leader, colours = analyse_opening_leads(
                board, pr.get("contract"), pr.get("declarer"))
            if leader:
                lead_colours = {leader: colours}
        except Exception:
            lead_colours = {}

    img  = Image.new("RGB", (cell_w, cell_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    PAD  = max(4, cell_w // 70)

    fs_hand = max(10, cell_h // 19)
    fs_cmp  = max(9,  cell_h // 22)
    fs_hcp  = max(9,  cell_h // 22)
    fs_th   = max(8,  cell_h // 26)
    fs_tv   = max(9,  cell_h // 24)

    fhand = make_font(fs_hand)
    fcmp  = make_bold_font(fs_cmp)
    fhcpb = make_bold_font(fs_hcp + 2)
    fkr   = make_font(max(7, fs_hcp - 1))
    fth   = make_font(fs_th)
    ftv   = make_font(fs_tv)

    lh  = th(fhand) + 2
    hbh = 4 * lh

    compass_w = int(cell_w * 0.63)
    box_size  = max(46, min(compass_w // 3, cell_h // 4))
    box_x     = (compass_w - box_size) // 2
    _body_shift = round(1.5 * 1240 / 210) + round(2.0 * 1240 / 210)
    box_y = (cell_h - box_size) // 2 + _body_shift
    cx    = box_x + box_size // 2
    cy    = box_y + box_size // 2
    bx, by, bx2, by2 = box_x, box_y, box_x + box_size, box_y + box_size

    north_y = box_y - hbh - PAD * 2
    south_y = box_y + box_size + PAD * 2
    side_y  = box_y + (box_size - hbh) // 2

    vul    = board.get("vul", "None")
    ns_vul = vul in ("NS", "All")
    ew_vul = vul in ("EW", "All")
    ns_fill = (210, 50, 50) if ns_vul else (255, 255, 255)
    ew_fill = (210, 50, 50) if ew_vul else (255, 255, 255)

    draw.polygon([(bx, by),  (bx2, by),  (cx, cy)], fill=ns_fill)
    draw.polygon([(bx, by2), (bx2, by2), (cx, cy)], fill=ns_fill)
    draw.polygon([(bx, by),  (bx, by2),  (cx, cy)], fill=ew_fill)
    draw.polygon([(bx2, by), (bx2, by2), (cx, cy)], fill=ew_fill)

    dealer = board.get("dealer", "N").upper()
    dealer_axis_vul = {"N": ns_vul, "S": ns_vul, "E": ew_vul, "W": ew_vul}
    _cmp_inset = max(2, box_size // 10)
    compass_positions = [
        ("N", cx,              by + _cmp_inset,             "c"),
        ("W", bx + _cmp_inset, cy - th(fcmp) // 2,          "l"),
        ("E", bx2 - _cmp_inset, cy - th(fcmp) // 2,         "r"),
        ("S", cx,              by2 - th(fcmp) - _cmp_inset, "c"),
    ]
    for label, lx, ly, anchor in compass_positions:
        lw = tw(label, fcmp); lh_cmp = th(fcmp)
        tx_ = lx - lw//2 if anchor=="c" else (lx - lw if anchor=="r" else lx)
        if label == dealer:
            circ_r = max(lw, lh_cmp)//2 + 4
            circ_cx, circ_cy = tx_ + lw//2, ly + lh_cmp//2
            is_vul = dealer_axis_vul.get(label, False)
            circ_fill = (255,255,255) if is_vul else (210,50,50)
            ss = 4; disc_d = (circ_r*2+2)*ss
            disc_img = Image.new("RGBA", (disc_d, disc_d), (0,0,0,0))
            disc_draw = ImageDraw.Draw(disc_img)
            disc_draw.ellipse([0, 0, disc_d-1, disc_d-1], fill=circ_fill+(255,))
            disc_img = disc_img.resize((circ_r*2+2, circ_r*2+2), Image.LANCZOS)
            img.paste(disc_img, (circ_cx-circ_r-1, circ_cy-circ_r-1), disc_img)
            letter_color = (0,0,0) if is_vul else (255,255,255)
        else:
            letter_color = (0,0,0)
        draw.text((tx_, ly), label, fill=letter_color, font=fcmp)

    def draw_hand(hand_str, x0, y0, card_colours=None):
        hand = parse_hand(hand_str); y = y0; max_w = 0
        for suit in SUIT_ORDER:
            sym = SUIT_SYMBOL[suit]; ranks = hand[suit] or "-"
            sw_ = tw(sym, fhand)
            draw.text((x0, y), sym, fill=SUIT_COLOR[suit], font=fhand)
            if not card_colours or ranks == "-":
                # Αρχική συμπεριφορά (χωρίς χρωματισμό φύλλων)
                cw_ = tw(" "+ranks, fhand)
                draw.text((x0+sw_, y), " "+ranks, fill=(0,0,0), font=fhand)
                max_w = max(max_w, sw_+cw_)
            else:
                # Ίδια απόσταση με πριν: ένα κενό και μετά τα φύλλα με τη σειρά,
                # το καθένα με το χρώμα του (πράσινο / μαύρο / κόκκινο).
                rx = x0 + sw_
                draw.text((rx, y), " ", fill=(0,0,0), font=fhand)
                rx += tw(" ", fhand)
                for rank in ranks:
                    colour = card_colours.get(suit + rank, (0, 0, 0))
                    draw.text((rx, y), rank, fill=colour, font=fhand)
                    rx += tw(rank, fhand)
                max_w = max(max_w, rx - x0)
            y += lh
        return max_w

    nh  = parse_hand(board["north"])
    nw_ = max(tw(SUIT_SYMBOL[s]+" "+(nh[s] or "-"), fhand) for s in SUIT_ORDER)
    draw_hand(board["north"], (compass_w-nw_)//2, north_y, lead_colours.get("N"))

    sh_ = parse_hand(board["south"])
    sw_ = max(tw(SUIT_SYMBOL[s]+" "+(sh_[s] or "-"), fhand) for s in SUIT_ORDER)
    draw_hand(board["south"], (compass_w-sw_)//2, south_y, lead_colours.get("S"))

    wh  = parse_hand(board["west"])
    ww_ = max(tw(SUIT_SYMBOL[s]+" "+(wh[s] or "-"), fhand) for s in SUIT_ORDER)
    wx_ = max(PAD, bx - PAD - ww_)
    draw_hand(board["west"], wx_, side_y, lead_colours.get("W"))
    draw_hand(board["east"], bx2+PAD, side_y, lead_colours.get("E"))

    fbnum_bi = make_bold_italic_font(44)
    draw.text((PAD+8, PAD+40), str(board["board"]), fill=(0,0,0), font=fbnum_bi)

    hcp_n = calc_hcp(board["north"]); kr_n = calc_krhcp(board["north"])
    hcp_s = calc_hcp(board["south"]); kr_s = calc_krhcp(board["south"])
    hcp_e = calc_hcp(board["east"]);  kr_e = calc_krhcp(board["east"])
    hcp_w = calc_hcp(board["west"]);  kr_w = calc_krhcp(board["west"])
    hcol  = (50, 50, 50); krcol = (80, 80, 180)
    hcph  = th(fhcpb)

    DD_COL_SUITS   = ["C","D","H","S","NT"]
    DD_ROW_PLAYERS = ["N","S","E","W"]
    rh      = max(15, th(ftv)+6); hdr_h = rh
    lbl_cw  = max(18, tw("W",fth)+10)
    suit_cw = max(20, tw("13",ftv)+10)
    tbl_total_w = lbl_cw + 5*suit_cw
    tbl_total_h = hdr_h + len(DD_ROW_PLAYERS)*rh
    _ppm_dt = 1240/210; _dt_off = round(3.5*_ppm_dt/1.4142)
    dt_x = bx2 + _dt_off
    dt_y = by2 + _dt_off
    dt_x = max(PAD, min(dt_x, cell_w-PAD-tbl_total_w))
    dt_y = max(PAD, min(dt_y, cell_h-PAD-tbl_total_h))
    dt_x += round(5*1240/210) + round(3*1240/210)
    tbl_right = dt_x + tbl_total_w

    _ppm = 1240/210
    _hcp_w = round(10.0*_ppm); _hcp_h = round(7.5*_ppm)
    _hcp_rx2 = tbl_right; _hcp_ry1 = north_y - 4
    _hcp_rx1 = _hcp_rx2 - _hcp_w - round(10*_ppm)
    _hcp_ry2 = _hcp_ry1 + _hcp_h + 20
    _hcp_cx  = (_hcp_rx1+_hcp_rx2)//2; _hcp_cy = (_hcp_ry1+_hcp_ry2)//2

    def draw_hcp_kr(x, y, hcp_val, kr_val, anchor="c"):
        hstr = str(hcp_val); kstr = "({})" .format(kr_val)
        hw_ = tw(hstr, fhcpb); kw_ = tw(kstr, fkr); gap = 2
        total_w = hw_+gap+kw_
        sx = x-total_w//2 if anchor=="c" else (x-total_w if anchor=="r" else x)
        draw.text((sx, y), hstr, fill=hcol, font=fhcpb)
        draw.text((sx+hw_+gap, y+hcph//2-th(fkr)//2), kstr, fill=krcol, font=fkr)

    draw_hcp_kr(_hcp_cx, _hcp_ry1+1,        hcp_n, kr_n, "c")
    draw_hcp_kr(_hcp_cx, _hcp_ry2-hcph-1,   hcp_s, kr_s, "c")
    draw_hcp_kr(_hcp_rx1-10, _hcp_cy-hcph//2, hcp_w, kr_w, "l")
#    draw_hcp_kr(_hcp_rx2-2, _hcp_cy-hcph//2, hcp_e, kr_e, "r")
    draw_hcp_kr(_hcp_rx2 - 5, _hcp_cy-hcph//2, hcp_e, kr_e, "r")

    if dds_table:
        draw.rectangle([dt_x, dt_y, dt_x+tbl_total_w-1, dt_y+hdr_h-1], fill=(220,220,240))
        for ci, suit in enumerate(DD_COL_SUITS):
            cx_ = dt_x+lbl_cw+ci*suit_cw
            sym, sc = ("NT",(0,0,100)) if suit=="NT" else (SUIT_SYMBOL[suit], SUIT_COLOR[suit])
            draw.text((cx_+(suit_cw-tw(sym,fth))//2, dt_y+1), sym, fill=sc, font=fth)
        for ri, player in enumerate(DD_ROW_PLAYERS):
            ry  = dt_y+hdr_h+ri*rh
            rbg = (245,245,255) if ri%2==0 else (255,255,255)
            draw.rectangle([dt_x, ry, dt_x+tbl_total_w-1, ry+rh-1], fill=rbg)
            draw.text((dt_x+(lbl_cw-tw(player,fth))//2, ry+1), player, fill=(0,0,100), font=fth)
            for ci, suit in enumerate(DD_COL_SUITS):
                tricks = dds_table.get(player,{}).get(suit,0)
                level  = tricks - 6
                if level <= 0: continue
                vs = str(level); vx = dt_x+lbl_cw+ci*suit_cw
                if level==7: vc=(140,0,140)
                elif level==6: vc=(0,100,0)
                elif level>={"NT":3,"S":4,"H":4,"D":5,"C":5}.get(suit,4): vc=(0,0,180)
                else: vc=(0,0,0)
                draw.text((vx+(suit_cw-tw(vs,ftv))//2, ry+1), vs, fill=vc, font=ftv)
        draw.rectangle([dt_x, dt_y, dt_x+tbl_total_w-1, dt_y+tbl_total_h-1],
                       outline=(150,150,150), width=1)
        draw.line([(dt_x+lbl_cw, dt_y),(dt_x+lbl_cw, dt_y+tbl_total_h-1)],
                  fill=(150,150,150), width=1)
        draw.line([(dt_x, dt_y+hdr_h),(dt_x+tbl_total_w-1, dt_y+hdr_h)],
                  fill=(150,150,150), width=1)

    if caption:
        fcap   = make_bold_italic_font(max(7, fs_th+4))
        cap_lh = th(fcap)+1; RED=(200,0,0); BLACK=(40,40,40)
        lines  = caption.split("\n")
        max_w  = cell_w - 2*PAD
        for li, line in enumerate(lines):
            line_max_w = int(cell_w*0.80) if li==0 else max_w
            tmp_w = max(cell_w*3, sum(tw(ch,fcap) for ch in line)+4)
            line_h = th(fcap)
            tmp = Image.new("RGBA",(tmp_w, line_h+4),(255,255,255,0))
            tdraw = ImageDraw.Draw(tmp); x_=0
            for ch in line:
                color = RED if ch in ("♥","♦") else BLACK
                tdraw.text((x_,0), ch, fill=color+(255,), font=fcap); x_+=tw(ch,fcap)
            text_w = x_
            tmp = tmp.crop((0,0,text_w,line_h+4))
            if text_w > line_max_w:
                tmp = tmp.resize((line_max_w, line_h+4), Image.LANCZOS)
                paste_x = (cell_w-line_max_w)//2
            else:
                paste_x = max(PAD,(cell_w-text_w)//2)
            img.paste(tmp,(paste_x, 2+li*cap_lh), tmp)

    draw.rectangle([0,0,cell_w-1,cell_h-1], outline=(160,160,160), width=1)
    return img

def render_boards(boards, pair_results=None):
    cell_w = (A4_W - 2*MARGIN - 2*PADDING) // COLS
    cell_h = (A4_H - 2*MARGIN - 2*PADDING) // ROWS
    images = []
    for board in boards:
        img = render_board(board, cell_w, cell_h, pair_results=pair_results)
        images.append(img)
    return images, cell_w, cell_h

def assemble_pages_to_bytes(images, cell_w, cell_h, header="", pair_results=None, boards=None):
    n_pages = math.ceil(len(images) / BOARDS_PER_PAGE)
    pages   = []
    fhdr    = make_bold_font(28)
    fpg     = make_bold_font(22)

    for page_idx in range(n_pages):
        page = Image.new("RGB", (A4_W, A4_H), (255,255,255))
        draw = ImageDraw.Draw(page)
        page_label = "{}/{}".format(page_idx + 1, n_pages)
        pg_w = tw(page_label, fpg)
        draw.text((A4_W - MARGIN - pg_w, 10), page_label, fill=(80,80,80), font=fpg)
        if header:
            draw.text((MARGIN, 10), header, fill=(30,30,30), font=fhdr)
        slice_ = images[page_idx*BOARDS_PER_PAGE:(page_idx+1)*BOARDS_PER_PAGE]
        for i, img in enumerate(slice_):
            row = i // COLS; col = i % COLS
            x = MARGIN + col*(cell_w + PADDING)
            y = MARGIN + PADDING + row*(cell_h + ROW_GAP)
            if header: y += 40
            page.paste(img, (x, y))
        pages.append(page)

    buf = io.BytesIO()
    if pages:
        pages[0].save(buf, format="PDF", save_all=True,
                      append_images=pages[1:], resolution=150)
    buf.seek(0)
    # Τελευταία σελίδα: υπόμνημα
    return buf.read()

# ---------------------------------------------------------------------------
# ΚΛΙΜΑΚΑ ΑΠΟΤΕΛΕΣΜΑΤΩΝ ΑΝΑ ΔΙΑΝΟΜΗ
# Κάθε διανομή = διάγραμμα + κάθετη μπάρα ποσοστών με όλα τα συμβόλαια που
# παίχτηκαν (2 διανομές ανά σειρά x 5 σειρές = 10 διανομές ανά σελίδα).
# Τα αποτελέσματα όλων των ζευγαριών διαβάζονται από τη σελίδα "traveller"
# κάθε διανομής και αποθηκεύονται προσωρινά (st.cache_data) ώστε να μην
# ξαναζητούνται από τον server της ΕΟΜ.
# ---------------------------------------------------------------------------
import time

DECL_GREEK = {"N": "Β", "E": "Α", "S": "Ν", "W": "Δ"}

_CONTRACT_CELL_RE = re.compile(
    r'\s*([1-7])\s*(NT|ΧΑ|N|S|H|D|C|♠|♥|♦|♣)\s*'
    r'(XX|X|Χ|\*\*|\*)?\s*(=|[+\-−–]\s*\d+)?\s*',
    re.IGNORECASE)
_RESULT_CELL_RE = re.compile(r'\s*(=|[+\-−–]\s*\d+)\s*')
_PASS_CELL_RE   = re.compile(r'\s*(PASS|P|ΠΑΣΟ|ΠΑΣ)\s*', re.IGNORECASE)


def duplicate_score(level, denom, dbl, res, vul):
    """
    Score for the declaring side. level 1-7, denom in NT/S/H/D/C,
    dbl 0/1/2 (none / doubled / redoubled), res = tricks over(+)/under(-)
    the contract (0 = made exactly), vul = declarer side vulnerable.
    """
    mult = (1, 2, 4)[dbl]
    if res < 0:
        n = -res
        if dbl == 0:
            return -n * (100 if vul else 50)
        if vul:
            pts = 200 + 300 * (n - 1)
        else:
            pts = {1: 100, 2: 300}.get(n, 500 + 300 * (n - 3))
        return -pts * (2 if dbl == 2 else 1)

    if denom == "NT":
        trick = 40 + 30 * (level - 1)
        ot    = 30
    elif denom in ("S", "H"):
        trick = 30 * level
        ot    = 30
    else:
        trick = 20 * level
        ot    = 20
    trick *= mult
    score = trick
    score += (500 if vul else 300) if trick >= 100 else 50
    if level == 6:
        score += 750 if vul else 500
    elif level == 7:
        score += 1500 if vul else 1000
    if dbl == 1:
        score += 50
    elif dbl == 2:
        score += 100
    if dbl == 0:
        score += res * ot
    elif dbl == 1:
        score += res * (200 if vul else 100)
    else:
        score += res * (400 if vul else 200)
    return score


def parse_traveller_html(html):
    """
    Generic parser for one board's traveller page. For every table row that
    has a contract cell (e.g. '4♠ +1', '3ΧΑ=', '2♥ X -1') followed by the
    declarer cell (Β/Ν/Α/Δ or N/S/E/W) it returns one dict:
        {level, denom, dbl, res, declarer}   or   {"pass": True}
    The score is NOT read from the page - it is recomputed from the contract,
    result and vulnerability, so the page's sign convention does not matter.
    """
    suit_map = {"icon-spade": "♠", "icon-heart": "♥",
                "icon-diamond": "♦", "icon-club": "♣"}
    for cls, sym in suit_map.items():
        html = re.sub(r'<i[^>]*' + cls + r'[^>]*>(\s*</i>)?', sym, html)

    # drop mobile-only helper text such as <span class="sm:hidden">Ν: </span>
    html = re.sub(r'<span[^>]*sm:hidden[^>]*>.*?</span>', '', html,
                  flags=re.DOTALL | re.IGNORECASE)

    def cell_text(td):
        s = re.sub(r'<[^>]+>', ' ', td)
        s = s.replace("&nbsp;", " ").replace("&amp;", "&")
        s = re.sub(r'&#[0-9]+;', '', s)
        return re.sub(r'\s+', ' ', s).strip()

    denom_map = {"NT": "NT", "ΧΑ": "NT", "N": "NT", "S": "S", "H": "H",
                 "D": "D", "C": "C", "♠": "S", "♥": "H", "♦": "D", "♣": "C"}

    out = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE):
        cells = [cell_text(c) for c in
                 re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL | re.IGNORECASE)]
        if len(cells) < 3:
            continue
        for ci, txt in enumerate(cells):
            m = _CONTRACT_CELL_RE.fullmatch(txt)
            if m and txt.strip():
                decl = None
                for cand in cells[ci + 1: ci + 3]:
                    if len(cand) <= 8:
                        d = normalize_declarer(cand)
                        if d:
                            decl = d
                            break
                if not decl:
                    continue
                res_txt = m.group(4)
                if res_txt is None:
                    # result in its own cell: must look like =, +1 ... -13
                    # (a score such as -110 is NOT accepted as a result)
                    for cand in cells[ci + 1: ci + 4]:
                        if _RESULT_CELL_RE.fullmatch(cand):
                            v = re.sub(r'\s+', '', cand).replace("−", "-").replace("–", "-")
                            if v == "=" or abs(int(v)) <= 13:
                                res_txt = cand
                                break
                if res_txt is None:
                    res_txt = "="      # the site leaves the result blank when made exactly
                res_txt = re.sub(r'\s+', '', res_txt).replace("−", "-").replace("–", "-")
                res = 0 if res_txt == "=" else int(res_txt)
                if abs(res) > 13:
                    continue
                d_raw = (m.group(3) or "").upper()
                dbl = 2 if d_raw in ("XX", "**") else (1 if d_raw else 0)
                out.append({"level": int(m.group(1)),
                            "denom": denom_map[m.group(2).upper()],
                            "dbl": dbl, "res": res, "declarer": decl})
                break
            if _PASS_CELL_RE.fullmatch(txt) and ci >= 1:
                out.append({"pass": True})
                break
    return out


def summarise_board_field(rows, vul):
    """
    Group identical results and sort ascending by the N/S score
    (left = worst for N/S, right = best for N/S).
    Returns list of (ns_score, text, declarer_letter_greek, count).
    """
    groups = {}
    for r in rows:
        if r.get("pass"):
            key = ("PASS",)
        else:
            key = (r["level"], r["denom"], r["dbl"], r["res"], r["declarer"])
        groups[key] = groups.get(key, 0) + 1

    ns_vul = vul in ("NS", "All")
    ew_vul = vul in ("EW", "All")
    sym = {"NT": "ΧΑ", "S": "♠", "H": "♥", "D": "♦", "C": "♣"}
    items = []
    for key, cnt in groups.items():
        if key == ("PASS",):
            items.append((0, "PASS", "", cnt))
            continue
        level, denom, dbl, res, decl = key
        is_ns = decl in ("N", "S")
        sc = duplicate_score(level, denom, dbl, res, ns_vul if is_ns else ew_vul)
        ns_score = sc if is_ns else -sc
        res_txt = "=" if res == 0 else "{:+d}".format(res)
        text = "{}{}{}{}".format(level, sym[denom], "*" * dbl, res_txt)
        items.append((ns_score, text, DECL_GREEK[decl], cnt))
    items.sort(key=lambda t: (t[0], t[1], t[2]))
    return items


def _table_is_ew(table):
    """
    True when the pair-results 'table' cell (e.g. '3 ΑΔ', '3 ΒΝ', '1 EW',
    '1 NS') says the pair sat East/West.  The site uses Greek initials
    (Β=N, Ν=S, Α=E, Δ=W); English ones are accepted too.
    """
    parts = str(table or "").upper().split()
    if not parts:
        return False
    tok = re.sub(r"[^A-ZΑ-Ω]", "", parts[-1])
    if any(ch in tok for ch in ("Δ", "W")):
        return True
    if any(ch in tok for ch in ("Β", "Ν", "N", "S")):
        return False
    return "E" in tok


def _parse_pair_score_ns(pair_results, board_num=1):
    """Return the selected pair's board score in the same N/S scale as the
    summary page: positive = good for N/S, negative = good for E/W."""
    if not pair_results:
        return None
    res = pair_results.get(board_num)
    if not res:
        return None
    raw = str(res.get("score", "")).strip().replace("+", "")
    try:
        score = int(raw)
    except (TypeError, ValueError):
        return None
    # The pair-results table identifies the pair's orientation at the end
    # (e.g. "1 ΒΝ" / "1 ΑΔ", or "1 NS" / "1 EW").  Convert EW scores to the
    # N/S axis used by summarise_board_field().
    if _table_is_ew(res.get("table", "")):
        score = -score
    return score


SCALE_COLS       = 2
SCALE_ROWS       = 5
SCALE_PER_PAGE   = SCALE_COLS * SCALE_ROWS
SCALE_UNIT_GAP   = 30     # horizontal gap between the two boards of a row
SCALE_ROW_GAP    = 15     # vertical gap between rows
SCALE_TOP        = MARGIN + 60
_GAP_IMG_BAR     = 12
_BAR_W           = 15     # layout width (diagram size depends on it)
_BAR_EXTRA_L     = 4      # drawn bar is 25 % wider, growing to the left
_GAP_BAR_LABEL   = 10
_RIGHT_PAD       = 15


def _split_contract(full_text):
    """'3♥+1' -> ('3♥', '+1'),  '2♠=' -> ('2♠', '=')."""
    text = str(full_text).strip()
    for i, ch in enumerate(text):
        if ch in ("=", "+", "-"):
            return text[:i], text[i:]
    return text, ""


def _scale_fonts(cell_h):
    fs_hand = max(10, cell_h // 19)
    fcontract = make_font(fs_hand)
    try:
        f_italic = make_font(fs_hand, italic=True)
    except TypeError:
        f_italic = fcontract
    return fcontract, f_italic


def compute_scale_layout(all_items):
    """
    Work out ONE common geometry for every board of the report, so that all
    boards have the same size and the scales line up.
    all_items: list (one entry per board) of summarise_board_field() results.
    """
    inner_w = A4_W - 2 * MARGIN
    unit_w  = (inner_w - (SCALE_COLS - 1) * SCALE_UNIT_GAP) // SCALE_COLS
    cell_h  = (A4_H - 2 * MARGIN - 60
               - (SCALE_ROWS - 1) * SCALE_ROW_GAP) // SCALE_ROWS

    fcontract, f_italic = _scale_fonts(cell_h)

    max_base_w = max_res_w = max_cnt_w = 0
    for items in all_items:
        for _score, text, _decl, cnt in items:
            base, res = _split_contract(text)
            max_base_w = max(max_base_w, sum(tw(c, fcontract) for c in base))
            max_res_w  = max(max_res_w,  sum(tw(c, fcontract) for c in res))
            if cnt > 1:
                cs = " (x{})".format(cnt)
                max_cnt_w = max(max_cnt_w,
                                sum(tw(c, f_italic) for c in cs) + 6)

    label_w = (max_base_w + 6) + (max_res_w + 8) + max_cnt_w + 8
    overhead = _GAP_IMG_BAR + _BAR_W + _GAP_BAR_LABEL + label_w + _RIGHT_PAD
    cell_w = unit_w - overhead

    return {
        "unit_w": unit_w, "cell_w": cell_w, "cell_h": cell_h,
        "max_base_w": max_base_w, "max_res_w": max_res_w,
        "max_cnt_w": max_cnt_w, "label_w": label_w,
    }


def _make_label_image(full_text, cnt, L, fcontract, f_italic):
    """Contract label: base contract | right-aligned result | (xN)."""
    sym_to_suit = {v: k for k, v in SUIT_SYMBOL.items()}
    base_str, res_str = _split_contract(full_text)
    cnt_str = " (x{})".format(cnt) if cnt > 1 else ""

    col1_w = L["max_base_w"] + 6
    col2_w = L["max_res_w"] + 8
    space_shift = tw(" ", fcontract) * 2
    th_total = th(fcontract) + 4

    img = Image.new("RGBA", (L["label_w"] + 8, th_total + 8), (255, 255, 255, 0))
    idraw = ImageDraw.Draw(img)
    y_pos = 4

    x = 4
    for ch in base_str:
        color = (SUIT_COLOR[sym_to_suit[ch]] + (255,)) if ch in sym_to_suit \
            else (0, 0, 0, 255)
        idraw.text((x, y_pos), ch, font=fcontract, fill=color)
        x += tw(ch, fcontract)

    w_res = sum(tw(ch, fcontract) for ch in res_str)
    x = 4 + col1_w + (col2_w - w_res) - space_shift
    for ch in res_str:
        idraw.text((x, y_pos), ch, font=fcontract, fill=(0, 0, 0, 255))
        x += tw(ch, fcontract)

    if cnt_str:
        cnt_w = sum(tw(ch, f_italic) for ch in cnt_str) + 6
        cimg = Image.new("RGBA", (cnt_w, th_total + 4), (255, 255, 255, 0))
        cd = ImageDraw.Draw(cimg)
        cx = 2
        for ch in cnt_str:
            for dx in (0, 1):
                cd.text((cx + dx, y_pos), ch, font=f_italic,
                        fill=(100, 100, 100, 255))
            cx += tw(ch, f_italic)
        img.paste(cimg, (4 + col1_w + col2_w, 0), cimg)

    # the whole label (contract, result, count) is italic: slant the image
    # (a real oblique font is not shipped, so it is faked with a shear
    # centred on the middle of the text so nothing is pushed out of the image)
    h_img = img.height
    img = img.transform(img.size, Image.AFFINE,
                        (1, 0.2, -0.1 * h_img, 0, 1, 0),
                        resample=Image.BICUBIC)
    return img


# Colour convention of the bar.  True  -> top (higher %) red, bottom green
#                                False -> top green, bottom red
_BAR_TOP_IS_RED = True


def _pair_is_ew(pair_results, board_num):
    """True when the selected pair sat E/W on that board."""
    try:
        res = (pair_results or {}).get(board_num)
        return bool(res and _table_is_ew(res.get("table", "")))
    except Exception:
        return False


def _matchpoint_pct(scores_counts, x):
    """
    Percentage (0..100) that score x gets against the field.
    scores_counts: list of (score, count) from the point of view of the pair.
    2 points per result beaten, 1 per tie (own result excluded), over
    2 * (n - 1).  If x is not in the field it is treated as one more result.
    """
    below = sum(c for s, c in scores_counts if s < x)
    equal = sum(c for s, c in scores_counts if s == x)
    n = sum(c for _s, c in scores_counts)
    if equal == 0:
        equal, n = 1, n + 1
    if n <= 1:
        return 50.0
    return 100.0 * (2 * below + equal - 1) / (2.0 * (n - 1))


def _spread(desired, h, cmin, cmax, gap, strict=True):
    """
    Place labels (centres, bottom->top order, all of height h) as close as
    possible (least squares) to their desired positions, without overlapping
    and inside [cmin, cmax].  Returns None when strict and they don't fit.
    """
    n = len(desired)
    if n == 0:
        return []
    step = h + gap
    if strict and (n - 1) * step > (cmax - cmin) + 1e-6:
        return None
    if (n - 1) * step > (cmax - cmin) and n > 1:
        step = max(0.0, (cmax - cmin) / (n - 1))
    z = [desired[i] - i * step for i in range(n)]
    # isotonic regression (pool adjacent violators): z must be non-decreasing
    blocks = []                       # [sum, count]
    for v in z:
        blocks.append([v, 1])
        while len(blocks) > 1 and \
                blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, c = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    zs = []
    for s, c in blocks:
        zs += [s / c] * c
    lo, hi = cmin, cmax - (n - 1) * step
    return [min(max(zs[i], lo), hi) + i * step for i in range(n)]


def _draw_board_unit(page, d, b_img, items, target_score, x0, y0, L,
                     pair_ew=False):
    """
    Board image + vertical percentage bar + contract labels + frame.

    Every played result is scored (matchpoint %) from the point of view of
    the selected pair's orientation.  The bar is a % axis: 0 % at the bottom,
    100 % at the top.  The pair's own result sits at its own % (black line,
    which also splits the bar into a red and a green gradient that fade to
    almost white at the line); lower results are placed proportionally below
    the line, higher ones above it, and are only pushed apart when they would
    overlap.
    """
    fcontract, f_italic = _scale_fonts(L["cell_h"])
    th_main = th(fcontract)
    bw, bh = b_img.width, b_img.height

    unit_w = bw + _GAP_IMG_BAR + _BAR_W + _GAP_BAR_LABEL + L["label_w"] + _RIGHT_PAD
    x1, y1 = x0 + unit_w, y0 + bh

    # the board image carries its own thin grey border (legacy layout);
    # blank it so no line separates the diagram from the bar
    b_img = b_img.copy()
    ImageDraw.Draw(b_img).rectangle([0, 0, bw - 1, bh - 1],
                                    outline=(255, 255, 255), width=2)
    page.paste(b_img, (x0, y0))

    sign = -1 if pair_ew else 1
    # (score from the pair's point of view, text, decl, count)
    pitems = sorted([(sign * s, t, dcl, c) for s, t, dcl, c in items],
                    key=lambda it: (it[0], it[1], it[2]))

    # ---- bar geometry (room above/below for half a label) ----
    h_lbl = th_main + 2.0
    pad = h_lbl / 2.0 + 4
    v_bar_top = y0 + pad
    v_bar_bottom = y1 - pad
    H = float(v_bar_bottom - v_bar_top)
    v_bar_x = x0 + bw + _GAP_IMG_BAR
    bar_l = v_bar_x - _BAR_EXTRA_L

    # ---- percentages ----
    sc_counts = [(s, c) for s, _t, _d, c in pitems]
    if target_score is None:
        p0 = 50.0
    else:
        p0 = _matchpoint_pct(sc_counts, sign * target_score)
    u0 = p0 / 100.0 * H                       # line position, from the bottom

    if not pitems:      # no field data for this board: neutral bar, no split
        d.rectangle([bar_l, v_bar_top, v_bar_x + _BAR_W, v_bar_bottom],
                    fill=(225, 225, 225))
        d.rectangle([x0, y0, x1, y1], outline=(0, 0, 0), width=1)
        return unit_w

    # ---- gradient bar ----
    top_dark, top_pale = ((140, 0, 0), (252, 242, 242)) if _BAR_TOP_IS_RED \
        else ((0, 110, 30), (242, 252, 242))
    bot_dark, bot_pale = ((0, 110, 30), (242, 252, 242)) if _BAR_TOP_IS_RED \
        else ((140, 0, 0), (252, 242, 242))

    def lerp(a, b, t):
        return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))

    for yy in range(int(round(v_bar_top)), int(round(v_bar_bottom)) + 1):
        u = v_bar_bottom - yy
        if u >= u0:
            t = (u - u0) / (H - u0) if H > u0 else 0.0
            c = lerp(top_pale, top_dark, min(1.0, max(0.0, t)))
        else:
            t = (u0 - u) / u0 if u0 > 0 else 0.0
            c = lerp(bot_pale, bot_dark, min(1.0, max(0.0, t)))
        d.line([(bar_l, yy), (v_bar_x + _BAR_W, yy)], fill=c)

    # ---- the pair's own line ----
    line_y = v_bar_bottom - u0
    if target_score is not None:
        d.line([(bar_l - 3, line_y), (v_bar_x + _BAR_W + 4, line_y)],
               fill=(0, 0, 0), width=2)

    # ---- label positions: proportional to the %, no overlaps ----
    gap = 2.0
    pcts = [_matchpoint_pct(sc_counts, s) for s, _t, _d, _c in pitems]
    desired = [p / 100.0 * H for p in pcts]
    EPS = 1e-9
    idx_lo = [i for i, p in enumerate(pcts) if p < p0 - EPS]
    idx_mid = [i for i, p in enumerate(pcts) if abs(p - p0) <= EPS]
    idx_hi = [i for i, p in enumerate(pcts) if p > p0 + EPS]

    ys = [None] * len(pitems)
    placed = False
    if target_score is not None:
        mid_pos = _spread([u0] * len(idx_mid), h_lbl, 0.0, H, gap, strict=False)
        lo_max = u0 - h_lbl / 2.0 - 1
        hi_min = u0 + h_lbl / 2.0 + 1
        if idx_mid:
            lo_max = min(mid_pos) - h_lbl - gap
            hi_min = max(mid_pos) + h_lbl + gap
        lo_pos = _spread([desired[i] for i in idx_lo], h_lbl, 0.0, lo_max, gap)
        hi_pos = _spread([desired[i] for i in idx_hi], h_lbl, hi_min, H, gap)
        if lo_pos is not None and hi_pos is not None:
            for i, v in zip(idx_lo, lo_pos):
                ys[i] = v
            for i, v in zip(idx_mid, mid_pos):
                ys[i] = v
            for i, v in zip(idx_hi, hi_pos):
                ys[i] = v
            placed = True
    if not placed:      # not enough room on one side: plain no-overlap spread
        ys = _spread(desired, h_lbl, 0.0, H, gap, strict=False)

    # ---- paste the labels (centre of the text on the computed position) ----
    lab_x = int(v_bar_x + _BAR_W + _GAP_BAR_LABEL)
    for (s, t, _dcl, c), u in zip(pitems, ys):
        lbl = _make_label_image(t, c, L, fcontract, f_italic)
        cy = v_bar_bottom - u
        page.paste(lbl, (lab_x, int(round(cy - (4 + th_main / 2.0)))), lbl)

    d.rectangle([x0, y0, x1, y1], outline=(0, 0, 0), width=1)
    return unit_w


class _HttpError(Exception):
    def __init__(self, status):
        super().__init__("HTTP {}".format(status))
        self.status = status


@st.cache_data(ttl=900, show_spinner=False, max_entries=600)
def _fetch_text_cached(url):
    """
    Κατεβάζει μια σελίδα και την κρατά στη μνήμη για 15 λεπτά (κοινή για όλους
    τους χρήστες της εφαρμογής). Τα σφάλματα (εκτός από 404) ΔΕΝ αποθηκεύονται.
    Επιστρέφει "" για 404.
    """
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    if r.status_code == 404:
        return ""
    if not r.ok or len(r.content) <= 200:
        raise _HttpError(r.status_code)
    return _decode(r)


def _get_html(url, retries=2):
    for attempt in range(retries):
        try:
            return _fetch_text_cached(url) or None
        except _HttpError as exc:
            if exc.status in (403, 429, 503):      # μας περιορίζει ο server
                time.sleep(8 * (attempt + 1))
                continue
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    return None


def fetch_field_results(page_url, boards, progress_cb=None):
    """
    Επιστρέφει {αριθμός_διανομής: [γραμμές αποτελεσμάτων όλων των ζευγαριών]}.
    1 αίτημα ανά διανομή (+1 για τη σελίδα αποτελεσμάτων), με cache.
    """
    from urllib.parse import urljoin
    base = page_url.rstrip("/")
    main_html = _get_html(page_url) or ""
    links = {}
    for href in re.findall(r'href=["\']([^"\']+)["\']', main_html, re.IGNORECASE):
        m = re.search(r'/(?:boards?|traveller|travellers|travelers?)/(\d+)(?:[/?#]|$)',
                      href, re.IGNORECASE)
        if m:
            links.setdefault(int(m.group(1)), urljoin(page_url + "/", href))

    # Μοτίβο URL που μαθαίνουμε από τα links που βρέθηκαν (.../board/7 -> .../board/{})
    templates = []
    for n, u in links.items():
        t = re.sub(r'(?<=/)' + str(n) + r'(?=[/?#]|$)', '{}', u, count=1)
        if '{}' in t and t not in templates:
            templates.append(t)

    def known_urls(n):
        urls = ([links[n]] if n in links else []) + [t.format(n) for t in templates]
        return list(dict.fromkeys(urls))

    def guess_urls(n):
        known = set(known_urls(n))
        out = [base + p.format(n) for p in ("/board/{}", "/boards/{}", "/traveller/{}",
                                            "/travellers/{}", "/traveler/{}")]
        return [u for u in dict.fromkeys(out) if u not in known]

    def try_board(n):
        got_page = False
        for group in (known_urls(n), guess_urls(n)):
            for u in group:
                t0 = time.time()
                html = _get_html(u)
                if time.time() - t0 > 0.05:        # πραγματικό αίτημα (όχι cache)
                    time.sleep(0.4)                # ευγένεια προς τον server
                if not html:
                    continue
                rows = parse_traveller_html(html)
                if rows:
                    return rows
                got_page = True
            if got_page:
                break          # η σελίδα κατέβηκε αλλά δεν διαβάστηκε: δεν μαντεύουμε
        return []

    nums = []
    for b in boards:
        try:
            nums.append(int(b["board"]))
        except (TypeError, ValueError):
            continue

    field = {}
    for i, n in enumerate(nums):
        if progress_cb:
            progress_cb(i, len(nums), n)
        rows = try_board(n)
        if rows:
            field[n] = rows

    missing = [n for n in nums if n not in field]
    if missing:                        # δεύτερο πέρασμα (π.χ. προσωρινός περιορισμός)
        time.sleep(8)
        for n in missing:
            rows = try_board(n)
            if rows:
                field[n] = rows
            time.sleep(1.0)
    return field


def build_scale_items(boards, field_results):
    """Λίστα (μία ανά διανομή) με τα ομαδοποιημένα αποτελέσματα του πεδίου."""
    items = []
    for b in boards:
        try:
            rows = field_results.get(int(b["board"]), [])
            items.append(summarise_board_field(rows, b.get("vul", "None")))
        except Exception:
            items.append([])
    return items



def _scale_page_canvas(header, pi, num_pages):
    """Λευκή σελίδα Α4 με επικεφαλίδα και αρίθμηση (ίδιο στυλ με την κανονική)."""
    page = Image.new("RGB", (A4_W, A4_H), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    fhdr = make_bold_font(28)
    fpg  = make_bold_font(22)
    label = "{}/{}".format(pi + 1, num_pages)
    draw.text((A4_W - MARGIN - tw(label, fpg), 10), label,
              fill=(80, 80, 80), font=fpg)
    if header:
        draw.text((MARGIN, 10), header, fill=(30, 30, 30), font=fhdr)
    return page, draw


def render_scale_page(imgs, items_list, targets, L, header, pi, num_pages,
                      orients=None):
    """Μία σελίδα: έως 10 διανομές (5 σειρές x 2), καθεμία με τη μπάρα της."""
    canvas, draw = _scale_page_canvas(header, pi, num_pages)
    bh = L["cell_h"]
    total_w = SCALE_COLS * L["unit_w"] + (SCALE_COLS - 1) * SCALE_UNIT_GAP
    start_x = MARGIN + (A4_W - 2 * MARGIN - total_w) // 2
    for slot, img in enumerate(imgs):
        col = slot % SCALE_COLS
        row = slot // SCALE_COLS
        x = start_x + col * (L["unit_w"] + SCALE_UNIT_GAP)
        y = SCALE_TOP + row * (bh + SCALE_ROW_GAP)
        _draw_board_unit(canvas, draw, img, items_list[slot], targets[slot],
                         x, y, L,
                         pair_ew=bool(orients[slot]) if orients else False)
    return canvas


def assemble_scale_pages_to_bytes(images, scale_items, layout, header="",
                                  pair_results=None, boards=None):
    targets, orients = [], []
    for b in boards:
        try:
            targets.append(_parse_pair_score_ns(pair_results, int(b["board"])))
            orients.append(_pair_is_ew(pair_results, int(b["board"])))
        except Exception:
            targets.append(None)
            orients.append(False)

    num_pages = math.ceil(len(images) / SCALE_PER_PAGE)
    pages = []
    for pi in range(num_pages):
        s = pi * SCALE_PER_PAGE
        e = s + SCALE_PER_PAGE
        pages.append(render_scale_page(images[s:e], scale_items[s:e],
                                       targets[s:e], layout, header,
                                       pi, num_pages, orients[s:e]))
    buf = io.BytesIO()
    if pages:
        pages[0].save(buf, format="PDF", save_all=True,
                      append_images=pages[1:], resolution=150)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Personalized EOM Hand Records",
    page_icon="clubs.png",
    layout="centered"
)

# ── Session state init ───────────────────────────────────────────────────────
for key, default in [
    ("step", "disclaimer"),
    ("reg", None),
    ("tournaments", None),
    ("chosen_url", None),
    ("chosen_title", None),
    ("chosen_club", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── STEP 1: Disclaimer ───────────────────────────────────────────────────────
if st.session_state.step == "disclaimer":

# Δημιουργούμε δύο στήλες: μια μικρή για το logo και μια μεγάλη για τον τίτλο
    st.markdown("""
        <style>
        [data-testid="stHorizontalBlock"] {
            align-items: center;
            display: flex;
        }
        /* Προαιρετικά: Μειώνει το κενό πάνω από τον τίτλο */
        .stApp h1 {
            padding-top: 0rem;
        }
        </style>
        """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([0.07, 0.93]) 

    with col1:
        try:
            st.image("clubs.png", width=55)
        except:
            st.write("♣️")

    with col2:
        # Χρησιμοποιούμε h3 ή h2 αν το Title σου φαίνεται πολύ μεγάλο τώρα που μίκρυνε η εικόνα
        st.title("Personalized EOM Hand Records")

#    st.title(" Personalized EOM Hand Records")
    st.markdown("### Όροι Χρήσης Εφαρμογής")
    st.info(
        "Η χρήση αυτής της εφαρμογής παρέχεται δωρεάν, αποκλειστικά για "
        "ενημερωτικούς και βοηθητικούς σκοπούς.\n\n"
        "Δεν παρέχεται καμία εγγύηση, ρητή ή σιωπηρή, σχετικά με την ακρίβεια, "
        "την πληρότητα ή την ορθότητα των πληροφοριών που παράγονται από αυτή "
        "ούτε για την αδιάλειπτη λειτουργία της. Ο δημιουργός δεν φέρει καμία "
        "ευθύνη για τυχόν λάθη, παραλείψεις ή για οποιαδήποτε χρήση ή ερμηνεία "
        "του περιεχομένου από τρίτους.\n\n"
        "Σε καμία περίπτωση το παρόν υλικό δεν αντικαθιστά την πληροφορία που "
        "υπάρχει στον επίσημο ιστότοπο της ΕΟΜ ούτε τα έγγραφα που διανέμονται "
        "από τους διαιτητές κατά τη διάρκεια των αγώνων. Η πληροφορία στον "
        "ιστότοπο της ΕΟΜ και επίσημα έγγραφα αποτελούν τη μοναδική έγκυρη "
        "πηγή πληροφόρησης.\n\n"
        "Με την πρόσβαση και χρήση του περιεχομένου, αποδέχεστε τους παραπάνω όρους."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✔ Συμφωνώ", use_container_width=True, type="primary"):
            st.session_state.step = "auth"
            st.rerun()
    with col2:
        if st.button("✖ Δε συμφωνώ", use_container_width=True):
            st.error("Η εφαρμογή απαιτεί αποδοχή των όρων χρήσης.")
            st.stop()

# ── STEP 2: Authorization ────────────────────────────────────────────────────
elif st.session_state.step == "auth":

# Δημιουργούμε δύο στήλες: μια μικρή για το logo και μια μεγάλη για τον τίτλο
    st.markdown("""
        <style>
        [data-testid="stHorizontalBlock"] {
            align-items: center;
            display: flex;
        }
        /* Προαιρετικά: Μειώνει το κενό πάνω από τον τίτλο */
        .stApp h1 {
            padding-top: 0rem;
        }
        </style>
        """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([0.07, 0.93]) 

    with col1:
        try:
            st.image("clubs.png", width=55)
        except:
            st.write("♣️")

    with col2:
        # Χρησιμοποιούμε h3 ή h2 αν το Title σου φαίνεται πολύ μεγάλο τώρα που μίκρυνε η εικόνα
        st.title("Personalized EOM Hand Records")

#    st.title(" Personalized EOM Hand Records")
#    st.title(" Personalized EOM Hand Records")
    st.markdown("### Εξουσιοδότηση")
    reg = st.text_input("Αριθμός Μητρώου Αθλητή", placeholder="π.χ. 15672")
    if st.button("✔ Είσοδος", type="primary"):
        if not reg.strip():
            st.error("⚠ Ο αριθμός μητρώου δεν μπορεί να είναι κενός.")
        else:
            valid = load_valid_numbers()
            if reg.strip() not in valid:
                st.error(
                    "⛔ Η χρήση της εφαρμογής επιτρέπεται μόνο σε εξουσιοδοτημένους "
                    "χρήστες. Για να ζητήσετε πρόσβαση επικοινωνήστε με το tak_0000@yahoo.com"
                )
                st.stop()
            else:
                st.session_state.reg  = reg.strip()
                st.session_state.step = "pick"
                st.rerun()


# ── STEP 3: Pick tournament (Fixed Transition) ──────────────────────────────
elif st.session_state.step == "pick":

# Δημιουργούμε δύο στήλες: μια μικρή για το logo και μια μεγάλη για τον τίτλο
    st.markdown("""
        <style>
        [data-testid="stHorizontalBlock"] {
            align-items: center;
            display: flex;
        }
        /* Προαιρετικά: Μειώνει το κενό πάνω από τον τίτλο */
        .stApp h1 {
            padding-top: 0rem;
        }
        </style>
        """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([0.07, 0.93]) 

    with col1:
        try:
            st.image("clubs.png", width=55)
        except:
            st.write("♣️")

    with col2:
        # Χρησιμοποιούμε h3 ή h2 αν το Title σου φαίνεται πολύ μεγάλο τώρα που μίκρυνε η εικόνα
        st.title("Personalized EOM Hand Records")

#    st.title(" Personalized EOM Hand Records")
#    st.title(" Personalized EOM Hand Records")
    st.markdown("### Επιλογή Τουρνουά Τελευταίων Τριών Ημερών")

    if not st.session_state.tournaments:
        with st.spinner("Ανάκτηση δεδομένων..."):
            raw_t = scrape_tournament_list(max_page=8)
            # Φίλτρο 3 ημερών
            cutoff = date.today() - timedelta(days=3)
            st.session_state.tournaments = [
                t for t in raw_t 
                if parse_date_str(t.get("date","")) and parse_date_str(t.get("date","")) >= cutoff
            ]
            if not st.session_state.tournaments:
                st.session_state.tournaments = raw_t[:10] # Fallback αν το 3ήμερο είναι κενό

    import pandas as pd
    df = pd.DataFrame(st.session_state.tournaments)
    display_df = df[['date', 'club', 'title']].copy()
    display_df.columns = ['Ημερομηνία', 'Σωματείο', 'Τουρνουά']

    search_query = st.text_input("🔍 Αναζήτηση:", "")
    if search_query:
        mask = display_df.apply(lambda r: r.astype(str).str.contains(search_query, case=False).any(), axis=1)
        display_df = display_df[mask]

    # Πίνακας Επιλογής
    event = st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun"
    )

    if event.selection.rows:
        sel_idx = event.selection.rows[0]
        actual_idx = display_df.index[sel_idx]
        chosen = st.session_state.tournaments[actual_idx]
        
        st.success(f"📍 Επιλέχθηκε: **{chosen['title']}**")
        
        # ΕΔΩ ΕΙΝΑΙ Η ΔΙΟΡΘΩΣΗ: Αλλαγή σε "generate"
        if st.button("🚀 Δημιουργία PDF", type="primary"):
            st.session_state.chosen_url = chosen["url"]
            st.session_state.chosen_title = chosen["title"]
            st.session_state.chosen_club = chosen.get("club", "")
            st.session_state.step = "generate" # <--- Πρέπει να είναι "generate"
            st.rerun()

    if st.button("⬅ Πίσω"):
        st.session_state.tournaments = None
        st.session_state.step = "auth"
        st.rerun()

# ── STEP 4: Generate PDF ─────────────────────────────────────────────────────
elif st.session_state.step == "generate":

    # Initialization of error state
    if "error_msg" not in st.session_state:
        st.session_state.error_msg = None

    # UI Header logic (Logo + Title)
    st.markdown("""
        <style>
        [data-testid="stHorizontalBlock"] { align-items: center; display: flex; }
        .stApp h1 { padding-top: 0rem; }
        </style>
        """, unsafe_allow_html=True)
    
    col1, col2 = st.columns([0.07, 0.93]) 
    with col1:
        try:
            st.image("clubs.png", width=55)
        except:
            st.write("♣️")
    with col2:
        st.title("Personalized EOM Hand Records")

    # Αν υπάρχει σφάλμα, εμφάνισέ το και σταμάτησε την υπόλοιπη ροή
    if st.session_state.error_msg:
        st.error(st.session_state.error_msg)
        if st.button("⬅ Πίσω στα τουρνουά", type="primary"):
            st.session_state.error_msg = None
            st.session_state.chosen_url = None
            st.session_state.chosen_title = None
            st.session_state.step = "pick"
            st.rerun()
        st.stop()

    reg           = st.session_state.reg
    page_url      = st.session_state.chosen_url
    chosen_title  = st.session_state.chosen_title
    chosen_club   = st.session_state.chosen_club or ""

    st.markdown("**Τουρνουά:** {}".format(chosen_title))
    st.markdown("**Αριθμός Μητρώου:** {}".format(reg))

    if not DDS_AVAILABLE:
        st.warning("⚠ Η βιβλιοθήκη DDS (endplay) δεν είναι διαθέσιμη. "
                   "Το PDF θα παραχθεί χωρίς double-dummy analysis.")

    if st.button("🚀 Δημιουργία PDF", type="primary"):
        with st.spinner("Φόρτωση PBN…"):
            pbn_text = fetch_pbn_from_url(page_url)
        
        if not pbn_text:
            st.session_state.error_msg = "Δεν υπάρχουν διανομές για το τουρνουά που επιλέξατε."
            st.rerun()

        boards = parse_pbn(pbn_text)
        if not boards:
            st.session_state.error_msg = "Δεν βρέθηκαν boards στο PBN αρχείο."
            st.rerun()

        with st.spinner("Αναζήτηση αποτελεσμάτων αθλητή…"):
            header      = scrape_tournament_info(page_url)
            # Αφαίρεση "| ΕΟΜ |" (και παραλλαγές) από την επικεφαλίδα
            header = re.sub(r'\s*\|\s*ΕΟΜ\s*\|\s*', ' ', header, flags=re.IGNORECASE).strip()
            header = re.sub(r'\s*\|\s*EOM\s*\|\s*', ' ', header, flags=re.IGNORECASE).strip()
            header = re.sub(r'\s{2,}', '  ', header)
            # Αντικατάσταση "EOM" με το όνομα σωματείου αν υπάρχει
            if chosen_club:
                header = re.sub(r'\bEOM\b', chosen_club, header, flags=re.IGNORECASE)
                if chosen_club not in header:
                    header = chosen_club + "  |  " + header
            card_url    = find_card_url(page_url, reg)
            pair_results = {}
            if card_url:
                pair_results = scrape_pair_results(card_url, page_url)
            else:
                st.session_state.error_msg = "Ο αθλητής δεν συμμετείχε στο τουρνουά που επιλέξατε."
                st.rerun()

        # Αποτελέσματα ΟΛΩΝ των ζευγαριών (για τη μπάρα κάθε διανομής)
        fprog = st.progress(0, text="Ανάκτηση αποτελεσμάτων όλων των ζευγαριών…")

        def _fprog(i, total, n):
            fprog.progress((i + 1) / max(1, total),
                           text="Αποτελέσματα διανομής {}/{}…".format(i + 1, total))

        field_results = {}
        try:
            field_results = fetch_field_results(page_url, boards, progress_cb=_fprog)
        except Exception:
            field_results = {}
        fprog.empty()

        layout, scale_items = None, None
        if field_results:
            scale_items = build_scale_items(boards, field_results)
            layout = compute_scale_layout(scale_items)
            no_data = [str(b["board"]) for b, it in zip(boards, scale_items) if not it]
            if no_data:
                st.warning("Δεν βρέθηκαν αποτελέσματα για τις διανομές: "
                           + ", ".join(no_data))
        else:
            st.warning("Δεν ήταν δυνατή η ανάκτηση των αποτελεσμάτων όλων των "
                       "ζευγαριών. Το PDF θα παραχθεί χωρίς μπάρες αποτελεσμάτων.")

        if layout:
            cell_w, cell_h = layout["cell_w"], layout["cell_h"]
        else:
            cell_w = (A4_W - 2*MARGIN - 2*PADDING) // COLS
            cell_h = (A4_H - 2*MARGIN - 2*PADDING) // ROWS

        progress_bar = st.progress(0, text="Υπολογισμός αντάμ…")
        images = []
        for i, board in enumerate(boards):
            progress_bar.progress((i+1)/len(boards),
                                  text="Υπολογισμός αντάμ {}/{}…".format(i+1, len(boards)))
            img = render_board(board, cell_w, cell_h,
                               pair_results=pair_results, header=header)
            images.append(img)
        progress_bar.empty()

        with st.spinner("Υπολογισμός αποτελεσμάτων…"):
            if layout:
                pdf_bytes = assemble_scale_pages_to_bytes(
                    images, scale_items, layout,
                    header=header, pair_results=pair_results, boards=boards)
            else:
                pdf_bytes = assemble_pages_to_bytes(
                    images, cell_w, cell_h,
                    header=header, pair_results=pair_results, boards=boards)

        safe_title = re.sub(r'[^\w]', '_', chosen_title)[:60] or "bridge"
        safe_club  = re.sub(r'[^\w]', '_', chosen_club)[:40] if chosen_club else ""
        filename   = (safe_club + "_" + safe_title if safe_club else safe_title) + ".pdf"

        # Καταγραφή download — αποτυχία δεν επηρεάζει την εφαρμογή
        log_download(reg, chosen_title, chosen_club, filename)

        st.success("✅ Το PDF είναι έτοιμο!")
        st.download_button(
            label="⬇ Λήψη PDF",
            data=pdf_bytes,
            file_name=filename,
            mime="application/pdf",
            use_container_width=True,
        )

    if st.button("← Πίσω στα τουρνουά"):
        st.session_state.error_msg = None
        st.session_state.step = "pick"
        st.rerun()
