#!/usr/bin/env python3
"""
Estimate Jewish vs non-Jewish distribution of unique attendees since Sept 1, 2025
based on first+last name heuristics.

Outputs:
- Per-person probability (Jewish 0..1)
- Aggregate: expected Jewish count (sum of probabilities) and category bins
"""

import os
import sys
import re
import csv
import psycopg2
import pandas as pd
from typing import Optional, Tuple
from dotenv import load_dotenv

# Load .env from repo root (this file lives in extra/jewish_name_analysis/)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

# ----------------------------------------------------------------------------
# Heuristic name scoring
# ----------------------------------------------------------------------------
# These lists are HEURISTICS only. Many names are ambiguous (e.g., Miller,
# Schwartz exists in both Jewish and German Christian populations; Cohen is
# very Jewish but Kohn/Kahn less so). Probabilities are best-effort priors.

# Highly distinctive Jewish surnames (priors ~0.9-0.97)
LASTNAME_HIGH = {
    "cohen": 0.95, "kohen": 0.95, "kahan": 0.9, "kahn": 0.7, "kohn": 0.7,
    "levy": 0.9, "levi": 0.85, "levin": 0.85, "levine": 0.9, "levitt": 0.9,
    "katz": 0.92, "kaplan": 0.9, "kagan": 0.85,
    "goldberg": 0.95, "goldstein": 0.95, "goldman": 0.9, "goldfarb": 0.95,
    "goldsmith": 0.6, "gold": 0.55,
    "silverstein": 0.92, "silverman": 0.92, "silver": 0.55,
    "rosenberg": 0.93, "rosenthal": 0.92, "rosenbaum": 0.93, "rosenblum": 0.93,
    "rosen": 0.85, "rose": 0.2,
    "weinstein": 0.95, "weinberg": 0.93, "weiner": 0.85, "wein": 0.7,
    "weiss": 0.6, "weissman": 0.9, "weisman": 0.9, "weissberg": 0.9,
    "friedman": 0.9, "friedmann": 0.9, "fried": 0.7, "friedland": 0.8,
    "schwartz": 0.7, "schwarz": 0.6, "shwartz": 0.85,
    "stein": 0.65, "steinberg": 0.93, "steinman": 0.85, "steiner": 0.65,
    "epstein": 0.95, "edelstein": 0.92, "bernstein": 0.95, "feinstein": 0.95,
    "lowenstein": 0.9, "loewenstein": 0.9, "blumenstein": 0.9,
    "rothstein": 0.93, "rothschild": 0.95, "roth": 0.6,
    "greenberg": 0.93, "greenblatt": 0.92, "greenwald": 0.85, "greenfield": 0.7,
    "bloom": 0.55, "blumenthal": 0.9, "blum": 0.7,
    "schneider": 0.45, "schneiderman": 0.9,
    "berkowitz": 0.95, "horowitz": 0.95, "rabinowitz": 0.97, "rabinovitz": 0.95,
    "moskowitz": 0.95, "leibowitz": 0.95, "abramowitz": 0.95, "lefkowitz": 0.95,
    "klein": 0.55, "kleiner": 0.6, "kleinman": 0.85,
    "shapiro": 0.92, "shapira": 0.92, "spiro": 0.8,
    "feldman": 0.92, "feld": 0.7, "feldstein": 0.92,
    "fischer": 0.3, "fisher": 0.25,
    "siegel": 0.9, "segal": 0.92, "segall": 0.9, "segel": 0.9,
    "stern": 0.6, "sternberg": 0.9, "sternfeld": 0.85,
    "halpern": 0.9, "halperin": 0.9, "alpern": 0.85,
    "abrams": 0.8, "abramson": 0.9, "abramoff": 0.9, "abramov": 0.85,
    "abram": 0.7, "abraham": 0.5, "abrahams": 0.75, "abrahamson": 0.85,
    "adler": 0.75, "altman": 0.85, "altmann": 0.85,
    "ackerman": 0.7, "ackermann": 0.6,
    "baron": 0.5, "barron": 0.3, "baruch": 0.9,
    "bauer": 0.2, "becker": 0.2, "berger": 0.55, "berg": 0.45,
    "bergman": 0.85, "bergmann": 0.8, "berman": 0.9, "berliner": 0.7,
    "blau": 0.65, "block": 0.5, "blank": 0.55, "blau": 0.65,
    "brenner": 0.6, "breuer": 0.55, "brown": 0.05, "browne": 0.05,
    "buchwald": 0.85, "buckman": 0.5,
    "caplan": 0.9, "chaikin": 0.85, "chaiken": 0.85,
    "davidoff": 0.85, "davidson": 0.35, "davidovitz": 0.95, "diamond": 0.55,
    "dorfman": 0.85, "drexler": 0.6, "dubin": 0.85, "dubinsky": 0.9,
    "ehrlich": 0.85, "eisenberg": 0.95, "eisen": 0.85, "eisner": 0.85,
    "elkin": 0.7, "elkins": 0.6, "ellis": 0.1,
    "engel": 0.5, "engelhardt": 0.3, "ettinger": 0.7,
    "fagin": 0.5, "falk": 0.6, "farber": 0.85, "feder": 0.7,
    "feigenbaum": 0.95, "feingold": 0.95, "fein": 0.85, "feinberg": 0.95,
    "feller": 0.5, "felman": 0.85,
    "fink": 0.5, "finkel": 0.85, "finkelstein": 0.97, "finkelman": 0.9,
    "firestone": 0.75, "fischel": 0.85, "fishman": 0.85, "flax": 0.5,
    "fogel": 0.85, "frank": 0.4, "frankel": 0.9, "franke": 0.4,
    "fram": 0.4, "fried": 0.7, "frydman": 0.95,
    "gaba": 0.4, "garber": 0.5, "garfield": 0.4, "garfinkel": 0.95,
    "gartner": 0.55, "gelb": 0.7, "gelber": 0.7, "geller": 0.7,
    "gerstein": 0.9, "gershon": 0.85, "ginsberg": 0.95, "ginsburg": 0.95,
    "glass": 0.4, "glasser": 0.7, "glazer": 0.7, "glassman": 0.85,
    "glick": 0.7, "glickman": 0.9, "glickstein": 0.92,
    "gluck": 0.7, "gold": 0.55, "goldenberg": 0.95, "goldfeld": 0.92,
    "goldhirsch": 0.95, "golding": 0.5, "goldner": 0.85, "goldwasser": 0.95,
    "goldwater": 0.85, "good": 0.05, "goodfriend": 0.7, "goodman": 0.55,
    "gordon": 0.3, "gottlieb": 0.85, "graff": 0.4, "granoff": 0.7,
    "grass": 0.2, "gross": 0.4, "grossman": 0.85, "grosz": 0.55,
    "gruber": 0.5, "grun": 0.3, "grunwald": 0.65,
    "hahn": 0.3, "halevi": 0.97, "halevy": 0.97,
    "handler": 0.5, "harkavy": 0.85, "harris": 0.05, "hart": 0.1,
    "hellman": 0.7, "heller": 0.55, "helman": 0.6, "herman": 0.4,
    "hertz": 0.7, "herzberg": 0.85, "herzfeld": 0.85, "herzl": 0.95,
    "hess": 0.2, "hirsch": 0.85, "hirschberg": 0.92, "hirschfeld": 0.92,
    "hirschhorn": 0.92, "hirschman": 0.9, "hirshberg": 0.92,
    "hodes": 0.7, "hoffman": 0.55, "hoffmann": 0.4, "hofstein": 0.85,
    "holzer": 0.4, "hurwitz": 0.95, "hyman": 0.55,
    "isaacs": 0.7, "isaacson": 0.7, "israel": 0.85,
    "jacobs": 0.5, "jacobson": 0.65, "jacoby": 0.7, "jaffe": 0.92, "jaffee": 0.92,
    "kafka": 0.7, "kahanovitz": 0.95, "kahane": 0.9, "kalman": 0.7,
    "kane": 0.15, "kantor": 0.7, "kantrowitz": 0.95,
    "kasdan": 0.85, "kasen": 0.7, "kasper": 0.2, "kassel": 0.6,
    "kaufman": 0.7, "kaufmann": 0.55, "kavner": 0.7,
    "kazin": 0.7, "kessler": 0.6, "kestenbaum": 0.92,
    "kimmel": 0.6, "kimmelman": 0.85, "kirsch": 0.55, "kirschner": 0.6,
    "klau": 0.6, "klausner": 0.85, "klig": 0.6, "kligman": 0.85,
    "kogan": 0.85, "kohl": 0.3, "kolski": 0.5, "kornblum": 0.85,
    "korn": 0.55, "kornberg": 0.9, "kosher": 0.95, "kotler": 0.85,
    "kotlowitz": 0.95, "kovner": 0.85, "kowalski": 0.05,
    "krakow": 0.7, "kramer": 0.45, "kranz": 0.55, "krass": 0.5,
    "kraus": 0.5, "krause": 0.3, "krieger": 0.45,
    "kuhn": 0.3, "kushner": 0.85, "kupferberg": 0.92,
    "landau": 0.85, "landauer": 0.85, "lander": 0.55, "landsman": 0.85,
    "lang": 0.1, "lansky": 0.85, "lapin": 0.7,
    "laskin": 0.65, "lasky": 0.6, "lazar": 0.7, "lazarus": 0.7,
    "lefkowitz": 0.95, "lehman": 0.6, "lehmann": 0.45,
    "lerner": 0.85, "lev": 0.7, "levenson": 0.92, "levenstein": 0.95,
    "leventhal": 0.92, "leibowitz": 0.95, "leiber": 0.6, "leibman": 0.85,
    "lessing": 0.5, "lesser": 0.55, "lev": 0.7, "lewin": 0.85,
    "lewis": 0.05, "libman": 0.85, "licht": 0.65, "lichtenberg": 0.9,
    "lichtenstein": 0.92, "lieb": 0.65, "lieber": 0.65, "lieberman": 0.92,
    "liebermann": 0.9, "liebowitz": 0.95, "lifshitz": 0.95, "lifsh": 0.85,
    "lipkin": 0.85, "lipman": 0.85, "lippman": 0.85, "lippmann": 0.85,
    "lipschitz": 0.95, "lipshitz": 0.95, "lipsky": 0.85, "lipton": 0.4,
    "litvak": 0.85, "loeb": 0.85, "loew": 0.7, "lowy": 0.85,
    "luria": 0.85, "lurie": 0.85, "lustig": 0.6,
    "mahler": 0.55, "maizlish": 0.85, "malkin": 0.7,
    "mandel": 0.7, "mandelbaum": 0.92, "manischewitz": 0.97,
    "marcus": 0.45, "margolin": 0.85, "margolis": 0.92, "margulies": 0.92,
    "markowitz": 0.95, "marx": 0.5, "maslow": 0.7,
    "mayer": 0.3, "meir": 0.85, "meisel": 0.7, "meister": 0.2,
    "melamed": 0.9, "melnick": 0.6, "mendel": 0.7, "mendelson": 0.85,
    "mendelsohn": 0.9, "mendelssohn": 0.85, "mendes": 0.5,
    "messinger": 0.45, "metzger": 0.3, "meyers": 0.25, "meyer": 0.25,
    "michaelson": 0.5, "miller": 0.05, "milstein": 0.92, "milgrom": 0.85,
    "mintz": 0.85, "mishkin": 0.85, "mizrahi": 0.95, "morgenstern": 0.85,
    "morris": 0.1, "moskowitz": 0.95, "myers": 0.2,
    "nachman": 0.85, "nash": 0.05, "naumann": 0.2, "neiman": 0.5,
    "nemerov": 0.85, "neuberger": 0.7, "neumann": 0.4,
    "neuwirth": 0.7, "newman": 0.4, "newmann": 0.5,
    "nudelman": 0.85,
    "olshan": 0.55, "olshansky": 0.7, "oppenheim": 0.85, "oppenheimer": 0.85,
    "orenstein": 0.92, "orlovsky": 0.5, "ornstein": 0.92,
    "padwa": 0.7, "paley": 0.7, "palmer": 0.05, "panitz": 0.7,
    "passman": 0.7, "patkin": 0.6, "perel": 0.7, "perl": 0.7,
    "perlman": 0.92, "perlmutter": 0.92, "perlstein": 0.92,
    "peretz": 0.85, "perry": 0.05, "peters": 0.05, "phillips": 0.05,
    "picker": 0.4, "pincus": 0.85, "pinkus": 0.7, "pinsky": 0.85,
    "piven": 0.7, "platt": 0.3, "plaut": 0.55, "plotkin": 0.85,
    "pogrebin": 0.85, "polin": 0.55, "polonsky": 0.65, "polsky": 0.65,
    "pomerantz": 0.9, "popper": 0.45, "popkin": 0.55,
    "portnoy": 0.7, "posner": 0.7, "potok": 0.7, "preminger": 0.7,
    "press": 0.2, "presser": 0.4, "pressman": 0.6, "preston": 0.05,
    "price": 0.05, "prince": 0.05, "prinz": 0.5, "puder": 0.5,
    "rabinow": 0.92, "rabin": 0.85, "rabinovich": 0.95, "rabinovitch": 0.95,
    "raden": 0.4, "radner": 0.7, "radin": 0.6,
    "raphael": 0.5, "rapaport": 0.92, "rapoport": 0.92, "rappaport": 0.92,
    "raskin": 0.85, "ratner": 0.85, "rauh": 0.5, "ravitz": 0.85,
    "reich": 0.45, "reiner": 0.45, "reiss": 0.5, "reisman": 0.85,
    "reuben": 0.5, "richman": 0.7, "rifkin": 0.85, "rifkind": 0.85,
    "riklis": 0.7, "ringel": 0.6, "ritter": 0.2, "rivkin": 0.85,
    "robbins": 0.15, "roberts": 0.05, "rockower": 0.7,
    "rosenbloom": 0.93, "rosenfeld": 0.93, "rosengarten": 0.85,
    "rosenkrantz": 0.85, "rosenstein": 0.95, "rosenstock": 0.85,
    "rosenwald": 0.9, "rosenwasser": 0.9, "rosenzweig": 0.95,
    "rosin": 0.7, "roskin": 0.7, "rotenberg": 0.9,
    "rothberg": 0.92, "rothblatt": 0.92, "rothfeld": 0.85, "rothkopf": 0.85,
    "rothman": 0.92, "rothschild": 0.97, "rothstein": 0.93,
    "rubel": 0.55, "ruben": 0.55, "rubin": 0.85, "rubinow": 0.85,
    "rubinstein": 0.95, "ruda": 0.4, "ruder": 0.4, "rudin": 0.7,
    "rudner": 0.6, "rudolph": 0.1, "russak": 0.4, "russakov": 0.7,
    "saban": 0.7, "sachs": 0.7, "sack": 0.4, "sackler": 0.7,
    "sacks": 0.5, "safran": 0.6, "sager": 0.4, "sagner": 0.5,
    "salant": 0.6, "salinger": 0.55, "salk": 0.7, "salomon": 0.55,
    "salzberg": 0.85, "salzman": 0.85, "samuels": 0.4, "samuel": 0.3,
    "samuelson": 0.5, "sandler": 0.6, "sapir": 0.65, "saphir": 0.65,
    "sarna": 0.5, "saron": 0.4, "savitsky": 0.65,
    "schachter": 0.85, "schaeffer": 0.3, "schapiro": 0.92,
    "scharf": 0.55, "schatz": 0.7, "schechter": 0.85, "scheider": 0.45,
    "scheidlinger": 0.7, "scheinberg": 0.92, "schein": 0.7, "scheiner": 0.5,
    "scher": 0.55, "schiff": 0.85, "schiffer": 0.55, "schiffman": 0.85,
    "schiller": 0.3, "schimmel": 0.5, "schindler": 0.3,
    "schlachter": 0.4, "schleifer": 0.5, "schlesinger": 0.85,
    "schlossberg": 0.85, "schmidt": 0.05, "schmid": 0.05,
    "schmuel": 0.85, "schnabel": 0.3, "schoenberg": 0.85, "schoen": 0.55,
    "schoenfeld": 0.85, "schor": 0.6, "schorr": 0.7, "schreiber": 0.55,
    "schroeder": 0.05, "schub": 0.5, "schubert": 0.2, "schuck": 0.2,
    "schulman": 0.7, "schultz": 0.05, "schulz": 0.05,
    "schur": 0.5, "schussel": 0.5, "schuster": 0.4,
    "schwab": 0.3, "schwartz": 0.7, "schwartzman": 0.92, "schwarz": 0.55,
    "schwarzbach": 0.7, "schwarzberg": 0.85, "schwartzberg": 0.9,
    "seff": 0.5, "seff": 0.5, "segal": 0.92,
    "seidel": 0.4, "seidenberg": 0.85, "seidman": 0.7,
    "seligman": 0.85, "seligmann": 0.85, "selz": 0.45, "selzer": 0.5,
    "shaffer": 0.2, "shapiro": 0.92, "sharaf": 0.6, "sharon": 0.7,
    "shatz": 0.65, "shaw": 0.05, "sheinberg": 0.85, "sheinkopf": 0.85,
    "sherman": 0.45, "shifman": 0.85, "shilling": 0.1,
    "shimkin": 0.7, "shinder": 0.5, "shire": 0.2, "shochet": 0.85,
    "shor": 0.55, "shorr": 0.6, "shulman": 0.7, "shuster": 0.4,
    "siegel": 0.9, "siegelman": 0.85, "silberman": 0.92, "silberstein": 0.92,
    "silver": 0.55, "silverberg": 0.92, "silverfield": 0.7, "silverstone": 0.7,
    "simkin": 0.65, "simon": 0.2, "simons": 0.15, "singer": 0.55,
    "sirkin": 0.7, "skoler": 0.5, "slade": 0.1, "slater": 0.1,
    "slavin": 0.7, "slifka": 0.65, "slotnick": 0.65,
    "small": 0.05, "smart": 0.05, "smith": 0.01,
    "snyder": 0.15, "sobel": 0.7, "sobler": 0.5, "sokol": 0.55,
    "sokolow": 0.7, "soloff": 0.6, "solomon": 0.5, "solomons": 0.55,
    "soros": 0.7, "spector": 0.7, "speilberg": 0.9, "spielberg": 0.9,
    "spier": 0.5, "spiegel": 0.7, "spiegelman": 0.85, "spielman": 0.7,
    "spira": 0.85, "spitz": 0.55, "spivak": 0.85, "sporn": 0.55,
    "stahl": 0.3, "starkman": 0.6, "starr": 0.1,
    "stavsky": 0.6, "stein": 0.65, "steinbach": 0.5, "steinberg": 0.95,
    "steinbrenner": 0.3, "steinberger": 0.85, "steingold": 0.9, "steinhardt": 0.85,
    "steinmetz": 0.55, "steinwald": 0.7, "stelzer": 0.4,
    "stern": 0.6, "sternberg": 0.92, "sternberger": 0.85, "sternlieb": 0.85,
    "stoller": 0.4, "stolz": 0.4, "strauss": 0.5, "strausz": 0.45,
    "strazzulla": 0.05, "streit": 0.55, "streisand": 0.92, "stricker": 0.4,
    "sturm": 0.3, "susskind": 0.85, "swartz": 0.5, "swerdlow": 0.7,
    "sylvester": 0.05,
    "tabachnick": 0.85, "tabak": 0.7, "talmadge": 0.05, "tannenbaum": 0.92,
    "tartakoff": 0.7, "tarshish": 0.7, "tatum": 0.05, "tauber": 0.55,
    "taub": 0.55, "teitel": 0.7, "teitelbaum": 0.95, "teller": 0.4,
    "tenenbaum": 0.92, "thaler": 0.55, "tobias": 0.4, "toby": 0.1,
    "topol": 0.7, "tornberg": 0.85, "torres": 0.05,
    "treger": 0.5, "trubek": 0.6, "trupin": 0.65, "tucker": 0.05,
    "turk": 0.1, "tuvim": 0.85,
    "udell": 0.55, "ungar": 0.55, "urbach": 0.55, "uris": 0.7,
    "vainshtein": 0.92, "vexler": 0.7, "vinik": 0.7, "viner": 0.5,
    "vogel": 0.5, "volk": 0.4, "von": 0.05,
    "wachs": 0.5, "wagner": 0.05, "waldman": 0.85, "walinsky": 0.85,
    "wallach": 0.65, "wallenberg": 0.45, "wallerstein": 0.85,
    "walter": 0.05, "walters": 0.05, "ward": 0.05, "warner": 0.1,
    "wasserman": 0.85, "watkins": 0.05, "wax": 0.4, "waxman": 0.85,
    "weber": 0.05, "weichert": 0.4, "weidberg": 0.85, "weidenfeld": 0.85,
    "weil": 0.7, "weiler": 0.55, "weill": 0.7, "weiman": 0.7,
    "weinblatt": 0.92, "weinfeld": 0.92, "weingarten": 0.92, "weinger": 0.7,
    "weingold": 0.92, "weinrib": 0.85, "weinrich": 0.65, "weintraub": 0.95,
    "weisbart": 0.7, "weisberg": 0.92, "weisbord": 0.85, "weisbrod": 0.85,
    "weisburd": 0.85, "weisbuch": 0.85, "weisel": 0.7, "weisenfeld": 0.85,
    "weisfeld": 0.85, "weisman": 0.92, "weiss": 0.6, "weissbach": 0.6,
    "weisser": 0.55, "weissman": 0.92, "weissmann": 0.9, "weisz": 0.6,
    "weitz": 0.6, "weitzman": 0.92, "weitzner": 0.7, "welikson": 0.5,
    "wellington": 0.05, "wells": 0.05, "wenig": 0.55, "wernick": 0.6,
    "westheimer": 0.85, "wexler": 0.85, "white": 0.02, "wiener": 0.65,
    "wiesel": 0.92, "wieseltier": 0.85, "wilensky": 0.7, "wilf": 0.6,
    "williams": 0.01, "wilson": 0.02, "winkler": 0.4, "winograd": 0.85,
    "winston": 0.05, "winter": 0.1, "winters": 0.1, "wise": 0.1,
    "wishnow": 0.6, "wishny": 0.6, "wisotsky": 0.7, "witt": 0.05,
    "wittenberg": 0.5, "wolf": 0.25, "wolfe": 0.2, "wolff": 0.3,
    "wolfson": 0.55, "wolinsky": 0.7, "wolk": 0.5, "wolper": 0.6,
    "wood": 0.02, "woods": 0.02, "wright": 0.02, "wurman": 0.5,
    "wurzel": 0.55, "wyman": 0.1, "wynn": 0.05,
    "yablonsky": 0.5, "yaffe": 0.92, "yalow": 0.7, "yampolsky": 0.7,
    "yarmolinsky": 0.7, "yeshiva": 0.95, "yiddish": 0.95, "yontif": 0.95,
    "young": 0.02,
    "zacharia": 0.55, "zaks": 0.7, "zamel": 0.55, "zaslavsky": 0.7,
    "zaslow": 0.7, "zats": 0.5, "zauder": 0.5, "zavin": 0.55,
    "zeff": 0.6, "zeitlin": 0.85, "zelenko": 0.4, "zellman": 0.7,
    "zelman": 0.7, "zelnick": 0.6, "zerbe": 0.3, "zide": 0.5,
    "zilberman": 0.92, "zilkha": 0.55, "zimmer": 0.4, "zimmerman": 0.7,
    "zimring": 0.55, "zinn": 0.45, "zipperstein": 0.85, "zola": 0.05,
    "zoll": 0.4, "zollman": 0.65, "zorach": 0.7, "zucker": 0.7,
    "zuckerberg": 0.95, "zuckerman": 0.92, "zuckerwise": 0.85,
    "zudkewich": 0.85, "zukor": 0.7, "zukerman": 0.92, "zukin": 0.65,
    "zur": 0.5, "zwerdling": 0.85, "zwick": 0.55, "zwicker": 0.5,
    "zwillinger": 0.6, "zyskind": 0.85,
}

# First names that strongly indicate Jewish background (Hebrew/Yiddish)
FIRSTNAME_HIGH = {
    "moshe": 0.97, "moishe": 0.97, "shlomo": 0.97, "shlomi": 0.95,
    "yitzhak": 0.97, "yitzchak": 0.97, "isaac": 0.4, "yitz": 0.92,
    "avi": 0.85, "aviv": 0.9, "aviva": 0.92, "avraham": 0.97, "avrum": 0.95,
    "abraham": 0.5, "abe": 0.45, "abram": 0.7,
    "ari": 0.7, "arie": 0.8, "aryeh": 0.92, "asher": 0.7,
    "ayelet": 0.92, "ayala": 0.85, "amir": 0.6, "amit": 0.7, "amitai": 0.9,
    "barak": 0.7, "baruch": 0.92, "boruch": 0.95, "ben": 0.15,
    "binyamin": 0.97, "benjamin": 0.45, "benji": 0.55, "benny": 0.4,
    "chana": 0.92, "channa": 0.92, "chaya": 0.92, "chaim": 0.97, "chayim": 0.97,
    "dov": 0.92, "dovid": 0.95, "david": 0.3, "dovi": 0.85,
    "daniel": 0.3, "danny": 0.2, "dani": 0.45, "devorah": 0.92, "deborah": 0.4,
    "dina": 0.55, "dinah": 0.7,
    "eitan": 0.92, "ethan": 0.25, "eliana": 0.85, "elia": 0.7,
    "eli": 0.55, "elie": 0.7, "eliyahu": 0.97, "elijah": 0.25,
    "eliezer": 0.95, "elisha": 0.7, "elad": 0.85, "elan": 0.7, "elon": 0.5,
    "ephraim": 0.95, "efraim": 0.95, "efrat": 0.92, "ezra": 0.85,
    "esther": 0.55, "esti": 0.85,
    "gabi": 0.6, "gavi": 0.85, "gabriel": 0.25, "gabriella": 0.2,
    "gad": 0.7, "gal": 0.65, "gali": 0.7, "galit": 0.85,
    "gideon": 0.65, "gidon": 0.85, "gilad": 0.92, "guy": 0.1,
    "hanna": 0.4, "hannah": 0.35, "hadassah": 0.95, "hadar": 0.85,
    "hagit": 0.92, "hila": 0.85, "hillel": 0.95,
    "ido": 0.85, "ilan": 0.85, "ilana": 0.85, "inbal": 0.85, "inbar": 0.85,
    "ira": 0.45, "irit": 0.85, "isadore": 0.6, "isidore": 0.6,
    "ivri": 0.85, "iyar": 0.85,
    "jacob": 0.35, "jake": 0.15, "jakob": 0.5, "jonah": 0.4, "jonas": 0.3,
    "joseph": 0.2, "joey": 0.1, "jordana": 0.55, "josh": 0.25, "joshua": 0.25,
    "jonathan": 0.25, "yonatan": 0.92, "yonatan": 0.92, "jonny": 0.15,
    "judah": 0.55, "yehuda": 0.97, "yehudah": 0.97, "judith": 0.4,
    "kfir": 0.92,
    "leah": 0.55, "lea": 0.4, "leor": 0.85, "leora": 0.85, "lior": 0.85,
    "liora": 0.85, "liron": 0.85, "lev": 0.5, "levi": 0.7,
    "maayan": 0.92, "maor": 0.85, "matan": 0.85, "mattan": 0.85, "matti": 0.6,
    "mayer": 0.55, "meir": 0.92, "menachem": 0.97, "mendel": 0.92, "mendy": 0.85,
    "michal": 0.55, "michael": 0.05, "miri": 0.7, "miriam": 0.6, "mirit": 0.85,
    "mordechai": 0.97, "mordy": 0.92,
    "naftali": 0.95, "nadav": 0.92, "natan": 0.7, "nathan": 0.3,
    "noa": 0.85, "noah": 0.25, "noam": 0.85, "naomi": 0.45,
    "ofer": 0.85, "ofir": 0.85, "ohad": 0.85, "omer": 0.55, "omri": 0.85,
    "orly": 0.85, "oren": 0.75, "ori": 0.7, "orit": 0.85, "or": 0.4,
    "rachel": 0.45, "rachael": 0.3, "rebekah": 0.4, "rebecca": 0.35,
    "rivka": 0.92, "rivkah": 0.95, "raanan": 0.85, "raphael": 0.55,
    "raz": 0.65, "razi": 0.7, "reut": 0.85,
    "ronit": 0.85, "ron": 0.1, "roni": 0.45, "ronen": 0.85, "ronen": 0.85,
    "rotem": 0.85, "ruben": 0.55, "reuben": 0.55,
    "ruth": 0.4, "ruti": 0.7,
    "sara": 0.4, "sarah": 0.3, "sari": 0.55, "shai": 0.7, "shay": 0.55,
    "shana": 0.6, "shanna": 0.4, "shana": 0.6, "shaina": 0.85, "shayna": 0.85,
    "sharon": 0.2, "shari": 0.5, "shauna": 0.05, "shavit": 0.85,
    "sheindel": 0.95, "sheindy": 0.95, "shifra": 0.92, "shifra": 0.92,
    "shimon": 0.95, "shimona": 0.92, "shira": 0.92, "shirit": 0.85,
    "shlomo": 0.97, "shmuel": 0.97, "shmuli": 0.97, "shmully": 0.95,
    "shoshana": 0.95, "shoshanna": 0.95, "shoshi": 0.92,
    "simcha": 0.95, "simchah": 0.95, "sivan": 0.85, "solomon": 0.55, "sol": 0.5,
    "tal": 0.7, "tali": 0.85, "talia": 0.7, "talya": 0.85, "tamar": 0.85,
    "tamara": 0.55, "tammy": 0.1, "tani": 0.7, "tanya": 0.1, "tehila": 0.92,
    "teitel": 0.85, "tova": 0.85, "tovah": 0.9, "tuvia": 0.92, "tzvi": 0.95,
    "tzipporah": 0.95, "tzippy": 0.95, "tzipora": 0.95,
    "uri": 0.7, "uriel": 0.85, "uziel": 0.85, "uzi": 0.85,
    "yael": 0.92, "yaakov": 0.97, "yair": 0.92, "yakov": 0.95,
    "yarden": 0.85, "yardena": 0.92, "yaron": 0.85,
    "yehoshua": 0.97, "yechiel": 0.95, "yedidya": 0.95, "yedidiah": 0.92,
    "yigal": 0.85, "yishai": 0.92, "yishaya": 0.92, "yisrael": 0.97,
    "yoav": 0.85, "yochanan": 0.95, "yochi": 0.92, "yoel": 0.92, "joel": 0.3,
    "yonah": 0.85, "yoni": 0.92, "yossi": 0.95, "yosef": 0.97, "yossef": 0.97,
    "yovel": 0.85, "ze'ev": 0.92, "zev": 0.85, "zevy": 0.92,
    "zachary": 0.2, "zack": 0.15, "zac": 0.15, "zalman": 0.92,
    "zelda": 0.7, "zelig": 0.85, "ziggy": 0.5, "zipporah": 0.95,
    "zissel": 0.7, "zohar": 0.85, "zoya": 0.3,
}

# Heuristic suffix-based bonuses for last names (small additive evidence)
SUFFIX_HINTS = [
    ("berg", 0.25),
    ("stein", 0.30),
    ("baum", 0.30),
    ("witz", 0.45),
    ("vitz", 0.40),
    ("owitz", 0.55),
    ("ovitz", 0.55),
    ("ovich", 0.30),
    ("blatt", 0.30),
    ("feld", 0.20),
    ("thal", 0.20),
    ("zweig", 0.45),
    ("kopf", 0.20),
    ("schitz", 0.45),
    ("shitz", 0.45),
    ("mann", 0.10),
    ("blum", 0.25),
]

# Strongly non-Jewish (Christian, Muslim, East Asian, South Asian, Latino,
# clearly ethnic) name parts — used to subtract / cap probability when present.
LASTNAME_NONJEWISH = {
    # Hispanic
    "garcia", "rodriguez", "martinez", "lopez", "hernandez", "gonzalez",
    "perez", "sanchez", "ramirez", "torres", "flores", "rivera", "gomez",
    "diaz", "reyes", "morales", "ortiz", "gutierrez", "ruiz", "chavez",
    "mendoza", "ramos", "vasquez", "vargas", "castillo", "jimenez",
    "moreno", "alvarez", "romero", "navarro", "santos", "delacruz",
    # East Asian
    "wang", "li", "zhang", "liu", "chen", "yang", "huang", "zhao", "wu",
    "zhou", "xu", "sun", "ma", "zhu", "lin", "hu", "guo", "he", "gao",
    "kim", "park", "choi", "jung", "kang", "yoon", "lim", "han", "shin",
    "kwon", "song", "ahn", "hwang", "ryu",
    "nguyen", "tran", "le", "pham", "huynh", "vo", "vu", "dang", "bui",
    "tanaka", "suzuki", "sato", "watanabe", "ito", "yamamoto", "nakamura",
    "kobayashi", "yoshida", "yamada", "sasaki", "matsumoto", "inoue",
    # South Asian
    "patel", "shah", "singh", "kumar", "sharma", "gupta", "verma", "agarwal",
    "mehta", "reddy", "rao", "iyer", "iyengar", "menon", "nair", "krishnan",
    "khan", "ahmed", "ali", "hussain", "malik", "qureshi", "siddiqui",
    "chopra", "kapoor", "sethi", "joshi", "kulkarni", "desai", "deshmukh",
    # Arab
    "hassan", "mohamed", "mohammed", "abdullah", "ibrahim", "ahmad",
    "abbas", "saleh", "rahman", "haque", "habib", "saad", "fadel",
    # African
    "okafor", "okonkwo", "adeyemi", "adebayo", "ade", "afolabi", "ojo",
    "mensah", "owusu", "asare", "boateng",
    # Italian
    "russo", "ferrari", "esposito", "ricci", "marino", "greco", "conti",
    "moretti", "bianchi", "romano", "colombo", "lombardi", "vitale",
    "bruno", "gallo", "rizzo", "barbieri", "fontana", "santoro",
    # Irish/Scottish
    "murphy", "kelly", "sullivan", "ryan", "oconnor", "obrien", "byrne",
    "mccarthy", "kennedy", "walsh", "mcdonald", "campbell", "stewart",
    "macdonald", "fitzgerald", "fitzpatrick", "doherty",
    # Generic English/Christian common
    "smith", "johnson", "williams", "jones", "brown", "davis",
    "wilson", "anderson", "thomas", "jackson", "harris",
    "robinson", "clark", "lewis", "walker", "hall", "allen",
    "young", "king", "wright", "scott", "green", "baker",
    "nelson", "carter", "mitchell", "roberts", "turner", "phillips",
    "parker", "evans", "edwards", "collins", "stewart", "morris",
    "murphy", "rogers", "reed", "cook", "morgan", "bailey",
    "cooper", "ward", "richardson", "cox", "howard", "long",
    "foster", "russell", "griffin", "diaz", "hayes", "myers",
    "ford", "hamilton", "graham", "sullivan", "wallace", "woods",
    "cole", "west", "jordan", "owens", "reynolds", "fisher",
    "ellis", "harrison", "gibson", "mcdonald", "cruz", "marshall",
    "ortiz", "gomez", "murray", "freeman", "wells", "webb",
    "simpson", "stevens", "tucker", "porter", "hunter", "hicks",
    "crawford", "henry", "boyd", "mason", "morales", "kennedy",
    "warren", "dixon", "ramos", "reyes", "burns", "gordon",
    "shaw", "holmes", "rice", "robertson", "hunt", "black",
    "daniels", "palmer", "mills", "nichols", "grant", "knight",
    "ferguson", "rose", "stone", "hawkins", "dunn", "perkins",
    "hudson", "spencer", "gardner", "stephens", "payne", "pierce",
    "berry", "matthews", "arnold", "wagner", "willis", "ray",
    "watkins", "olson", "carroll", "duncan", "snyder", "hart",
    "cunningham", "bradley", "lane", "andrews", "ruiz", "harper",
    "fox", "riley", "armstrong", "carpenter", "weaver", "greene",
    "lawrence", "elliott", "chavez", "sims", "austin", "peters",
    "kelley", "franklin", "lawson",
}

FIRSTNAME_NONJEWISH = {
    # Common Christian/Muslim/Hindu/East Asian first names that are very
    # unlikely Jewish. Used to downweight when paired with ambiguous lastname.
    "christopher", "christian", "christina", "christine", "kristin",
    "mary", "maria", "jose", "juan", "carlos", "miguel", "luis", "jesus",
    "francisco", "ricardo", "alejandro", "diego",
    "muhammad", "mohamed", "mohammed", "ahmed", "ali", "fatima", "aisha",
    "hassan", "omar", "ibrahim", "khalid", "yusuf", "salman",
    "wei", "ming", "ling", "fang", "hua", "yi", "jing", "xiao",
    "hyun", "jihye", "seojun", "minjun", "haeun",
    "yuki", "haruto", "ren", "akira", "sakura",
    "raj", "raja", "amit", "anjali", "deepak", "pooja", "ananya",
    "rahul", "priya", "neha", "aditya", "arjun", "krishna",
    "patrick", "padraig", "sean", "siobhan", "liam", "fiona",
    "giuseppe", "giovanni", "antonio", "marco", "luca", "lorenzo",
}


def normalize(name: str) -> str:
    if not name:
        return ""
    name = name.strip().lower()
    # remove accents (basic)
    import unicodedata
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-z'\- ]", "", name)
    return name


def score_lastname(last: str) -> Optional[float]:
    """Return Jewish probability prior in [0,1] for the last name, or None if unknown."""
    last_n = normalize(last)
    if not last_n:
        return None
    # exact whole-name lookup
    if last_n in LASTNAME_HIGH:
        return LASTNAME_HIGH[last_n]
    if last_n in LASTNAME_NONJEWISH:
        return 0.03
    # multi-part (hyphenated or two-word): take max of parts
    parts = re.split(r"[-\s]+", last_n)
    if len(parts) > 1:
        sub = [score_lastname(p) for p in parts if p]
        sub = [s for s in sub if s is not None]
        if sub:
            # take max signal
            return max(sub)
    # suffix-based heuristic
    suffix_boost = 0.0
    for suf, b in SUFFIX_HINTS:
        if last_n.endswith(suf):
            suffix_boost = max(suffix_boost, b)
    if suffix_boost > 0:
        # base prior ~0.20 + suffix boost, capped
        return min(0.20 + suffix_boost, 0.85)
    return None  # unknown


def score_firstname(first: str) -> Optional[float]:
    first_n = normalize(first)
    if not first_n:
        return None
    # take token before space (handles "Sarah Jane")
    first_n = first_n.split()[0] if first_n else ""
    if not first_n:
        return None
    if first_n in FIRSTNAME_HIGH:
        return FIRSTNAME_HIGH[first_n]
    if first_n in FIRSTNAME_NONJEWISH:
        return 0.03
    return None  # unknown


def combined_probability(first: str, last: str) -> Tuple[float, str]:
    """
    Combine first+last name signals into a single probability.
    Returns (probability, reasoning_label).

    Strategy:
    - If we have both signals, combine via odds ratio (Bayesian-ish).
    - If only one signal, use it but pull toward base rate.
    - If neither signal, return base rate (0.20).
    """
    base = 0.20  # base prior for unknown (college org context, agnostic)

    p_l = score_lastname(last)
    p_f = score_firstname(first)

    if p_l is None and p_f is None:
        return base, "unknown:both"

    def to_logit(p):
        p = min(max(p, 0.001), 0.999)
        import math
        return math.log(p / (1 - p))

    def from_logit(x):
        import math
        return 1 / (1 + math.exp(-x))

    base_logit = to_logit(base)

    if p_l is not None and p_f is not None:
        # both: sum log-odds adjustments from base
        l_logit = to_logit(p_l) - base_logit
        f_logit = to_logit(p_f) - base_logit
        combined = from_logit(base_logit + l_logit + f_logit)
        return combined, "both"
    if p_l is not None:
        # last name only — last name is more diagnostic; trust it but soften
        # slight pull toward base
        l_logit = to_logit(p_l) - base_logit
        combined = from_logit(base_logit + 0.85 * l_logit)
        return combined, "lastname_only"
    # first name only
    f_logit = to_logit(p_f) - base_logit
    combined = from_logit(base_logit + 0.7 * f_logit)
    return combined, "firstname_only"


# ----------------------------------------------------------------------------
# DB query
# ----------------------------------------------------------------------------

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('PGHOST'),
        port=os.getenv('PGPORT'),
        database=os.getenv('PGDATABASE'),
        user=os.getenv('PGUSER'),
        password=os.getenv('PGPASSWORD')
    )


def get_unique_attendees_since(date_str: str) -> pd.DataFrame:
    conn = get_db_connection()
    try:
        query = """
            SELECT DISTINCT p.id, p.first_name, p.last_name
            FROM people p
            INNER JOIN attendance a ON p.id = a.person_id
            INNER JOIN events e ON a.event_id = e.id
            WHERE a.checked_in = true
              AND e.start_datetime >= %s
            ORDER BY p.last_name, p.first_name
        """
        df = pd.read_sql(query, conn, params=(date_str,))
        return df
    finally:
        conn.close()


def main():
    print("=" * 70)
    print("JEWISH vs NON-JEWISH ATTENDEE DISTRIBUTION (name-based estimate)")
    print("Filter: unique checked-in attendees at events since 2025-09-01")
    print("=" * 70)
    print()

    df = get_unique_attendees_since("2025-09-01 00:00:00")
    if df.empty:
        print("No attendees found.")
        return

    rows = []
    for _, r in df.iterrows():
        p, reason = combined_probability(r["first_name"] or "", r["last_name"] or "")
        rows.append({
            "first_name": r["first_name"],
            "last_name": r["last_name"],
            "p_jewish": round(p, 3),
            "signal": reason,
        })

    out = pd.DataFrame(rows)

    # Categorize
    def bucket(p):
        if p >= 0.80:
            return "very likely Jewish (>=0.80)"
        if p >= 0.60:
            return "likely Jewish (0.60-0.80)"
        if p >= 0.40:
            return "uncertain (0.40-0.60)"
        if p >= 0.20:
            return "likely non-Jewish (0.20-0.40)"
        return "very likely non-Jewish (<0.20)"

    out["bucket"] = out["p_jewish"].apply(bucket)

    total = len(out)
    expected_jewish = out["p_jewish"].sum()
    expected_nonjewish = total - expected_jewish

    print(f"Total unique attendees since 2025-09-01: {total}")
    print()
    print(f"Expected Jewish (sum of probabilities):    {expected_jewish:.1f}  "
          f"({100*expected_jewish/total:.1f}%)")
    print(f"Expected non-Jewish (sum of 1 - p):        {expected_nonjewish:.1f}  "
          f"({100*expected_nonjewish/total:.1f}%)")
    print()

    print("Distribution by confidence bucket:")
    print("-" * 70)
    bucket_order = [
        "very likely Jewish (>=0.80)",
        "likely Jewish (0.60-0.80)",
        "uncertain (0.40-0.60)",
        "likely non-Jewish (0.20-0.40)",
        "very likely non-Jewish (<0.20)",
    ]
    counts = out["bucket"].value_counts().to_dict()
    for b in bucket_order:
        n = counts.get(b, 0)
        pct = 100 * n / total if total else 0
        print(f"  {b:<42} {n:>5}  ({pct:5.1f}%)")
    print()

    # Signal breakdown
    print("Coverage / signal source:")
    print("-" * 70)
    sig_counts = out["signal"].value_counts().to_dict()
    for s in ["both", "lastname_only", "firstname_only", "unknown:both"]:
        n = sig_counts.get(s, 0)
        pct = 100 * n / total if total else 0
        print(f"  {s:<20} {n:>5}  ({pct:5.1f}%)")
    print()

    # Write CSV
    out_path = os.path.join(_SCRIPT_DIR, "attendee_jewish_estimates.csv")
    out.sort_values("p_jewish", ascending=False).to_csv(out_path, index=False)
    print(f"Per-person estimates written to: {out_path}")

    # Top 30 most likely Jewish & most likely non-Jewish (for spot-check)
    print()
    print("Top 30 highest-probability Jewish (spot-check):")
    print("-" * 70)
    top = out.sort_values("p_jewish", ascending=False).head(30)
    for _, r in top.iterrows():
        print(f"  {r['p_jewish']:.2f}  {r['first_name']:<15} {r['last_name']:<25} "
              f"[{r['signal']}]")
    print()
    print("Bottom 30 lowest-probability Jewish (spot-check):")
    print("-" * 70)
    bot = out.sort_values("p_jewish", ascending=True).head(30)
    for _, r in bot.iterrows():
        print(f"  {r['p_jewish']:.2f}  {r['first_name']:<15} {r['last_name']:<25} "
              f"[{r['signal']}]")

    print()
    print("=" * 70)
    print("NOTE: Estimates are heuristic priors from a curated name list. They are")
    print("noisy for ambiguous names (Schwartz, Klein, Stein appear in both Jewish")
    print("and German-Christian populations). The 'expected' counts assume the")
    print("priors are well-calibrated; the bucket counts are usually more robust.")
    print("=" * 70)


if __name__ == "__main__":
    main()
