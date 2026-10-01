import os, sys, csv, json, datetime, webbrowser, subprocess, urllib.parse
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import customtkinter as ctk
from PIL import Image
import sys
import os
import sys
import csv
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv

# load_dotenv()
# ============================================================
# UNIK SYSTEM - INVOICE MANAGER PRO MAX (MongoDB natif)
# Devis, Factures (paiements partiels), Bons de commande,
# Stock, Clients/Fournisseurs, PDF pro, sauvegarde/restauration.
# pip install customtkinter reportlab pillow pymongo python-dotenv
# Placez "logo-unik-system.png" à côté de ce fichier.
# .env : MONGODB_URI=...   MONGODB_DB=app-unik-system
# ============================================================

VERSION = "PRO MAX"
import sys

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(APP_DIR, ".env"))
    

PDF_DIR = os.path.join(APP_DIR, "invoices")
DEFAULT_LOGO = os.path.join(APP_DIR, "logo-unik-system.png")
os.makedirs(PDF_DIR, exist_ok=True)

# Couleurs UNIK SYSTEM
RED, RED_D = "#EE2C29", "#C71F1C"
GRAY, DARK, LIGHT = "#6B6A65", "#3B3A37", "#F4F4F2"
GREEN, ORANGE = "#2E9E5B", "#D98A00"

STATUS_COLORS = {
    "Payée": GREEN,
    "Non payée": RED,
    "Partiellement payée": ORANGE,
    "Brouillon": GRAY,
    "Envoyé": ORANGE,
    "Accepté": GREEN,
    "Refusé": RED,
    "Converti": GRAY,
    "Confirmé": GREEN,
    "Reçu": GREEN,
    "Annulé": RED
}

RED, RED_D = "#EE2C29", "#C71F1C"
GRAY, DARK, LIGHT = "#6B6A65", "#3B3A37", "#F4F4F2"
GREEN, ORANGE = "#2E9E5B", "#D98A00"

STATUS_COLORS = {"Payée": GREEN, "Non payée": RED, "Partiellement payée": ORANGE, "Brouillon": GRAY,
                 "Envoyé": ORANGE, "Accepté": GREEN, "Refusé": RED, "Converti": GRAY,
                 "Confirmé": GREEN, "Reçu": GREEN, "Annulé": RED}
LIBRE = "— Saisie libre —"
MODES = ["Espèces", "Virement", "Chèque", "Carte", "Effet"]

DOCS = {
    "FAC": dict(label="Facture", plural="Factures", prefix="FAC", party="Client", party_pdf="FACTURÉ À",
                due_label="Échéance", days=30, statuses=["Non payée", "Partiellement payée", "Payée"],
                title="FACTURE", file="Facture", sign_right="Signature du client",
                arret="Arrêtée la présente facture à la somme de :"),
    "DEV": dict(label="Devis", plural="Devis", prefix="DEV", party="Client", party_pdf="CLIENT",
                due_label="Valable jusqu'au", days=30,
                statuses=["Brouillon", "Envoyé", "Accepté", "Refusé", "Converti"],
                title="DEVIS", file="Devis", sign_right="Bon pour accord (client)",
                arret="Arrêté le présent devis à la somme de :"),
    "BC": dict(label="Bon de commande", plural="Bons de commande", prefix="BC", party="Fournisseur",
               party_pdf="FOURNISSEUR", due_label="Livraison prévue", days=15,
               statuses=["Brouillon", "Envoyé", "Confirmé", "Reçu", "Annulé"],
               title="BON DE COMMANDE", file="BonCommande", sign_right="Cachet du fournisseur",
               arret="Arrêté le présent bon de commande à la somme de :"),
}

# ============================================================ MongoDB
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
MONGODB_DB = os.getenv("MONGODB_DB", "app-unik-system")
COLLS = ["settings", "products", "movements", "invoices", "items", "clients", "payments"]

DB, DB_ERROR = None, ""
try:
    _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    _client.admin.command("ping")
    DB = _client[MONGODB_DB]
    for coll, key, opts in [("products", "id", dict(unique=True)), ("products", "ref", dict(unique=True, sparse=True)),
                            ("invoices", "id", dict(unique=True)), ("invoices", "number", dict(unique=True)),
                            ("items", "invoice_id", {}), ("clients", "id", dict(unique=True)),
                            ("payments", "invoice_id", {}), ("movements", "product_id", {}),
                            ("settings", "key", dict(unique=True))]:
        try:
            DB[coll].create_index([(key, ASCENDING)], **opts)
        except Exception as ex:  # un index qui échoue ne doit pas bloquer l'application
            print("Index ignoré :", coll, key, ex)
    print(f"✅ MongoDB connecté : {MONGODB_DB}")
except Exception as e:
    DB_ERROR = str(e)
    print("❌ MongoDB non connecté :", e)


def next_id(name):
    """Compteur atomique, initialisé sur le plus grand id existant (compatible anciennes données)."""
    q = dict(filter={"_id": name}, update={"$inc": {"seq": 1}}, return_document=ReturnDocument.AFTER)
    c = DB.counters.find_one_and_update(**q)
    if c is None:
        last = DB[name].find_one(sort=[("id", DESCENDING)])
        try:
            DB.counters.insert_one({"_id": name, "seq": int(last["id"]) if last else 0})
        except DuplicateKeyError:
            pass
        c = DB.counters.find_one_and_update(**q)
    return c["seq"]


def init_db():
    defaults = {"company": "UNIK SYSTEM", "address": "", "phone": "", "email": "", "ice": "", "if": "",
                "rc": "", "tp": "", "rib": "",
                "conditions": "Paiement à 30 jours. Merci de votre confiance.",
                "conditions_devis": "Devis valable 30 jours. Acompte de 50% à la commande.",
                "conditions_bc": "Merci de confirmer la réception de ce bon de commande.",
                "logo": DEFAULT_LOGO if os.path.isfile(DEFAULT_LOGO) else ""}
    for k, v in defaults.items():
        DB.settings.update_one({"key": k}, {"$setOnInsert": {"value": v}}, upsert=True)


def setting(key):
    r = DB.settings.find_one({"key": key}) if DB is not None else None
    return r.get("value", "") if r else ""


# ============================================================ Utilitaires
def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def fmt(v):
    return f"{v:,.2f} DH".replace(",", " ")


def num(s):
    return float(str(s).strip().replace(",", ".").replace(" ", "") or 0)


def calc(items, disc_pct=0.0):
    ht = sum(i["qty"] * i["price"] for i in items)
    tva = sum(i["qty"] * i["price"] * i["vat"] / 100 for i in items)
    k = 1 - disc_pct / 100
    remise = ht * disc_pct / 100
    return ht, remise, ht - remise, tva * k, (ht + tva) * k


def open_file(path):
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.call(["open", path])
    else:
        subprocess.call(["xdg-open", path])


def next_number(prefix):
    y = datetime.date.today().year
    best = 0
    for d in DB.invoices.find({"number": {"$regex": f"^{prefix}-{y}-\\d+$"}}, {"number": 1, "_id": 0}):
        best = max(best, int(d["number"].rsplit("-", 1)[1]))
    return f"{prefix}-{y}-{best + 1:04d}"


# --- WORDS START (montant en lettres)
_U = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze",
      "treize", "quatorze", "quinze", "seize", "dix-sept", "dix-huit", "dix-neuf"]
_T = ["", "", "vingt", "trente", "quarante", "cinquante", "soixante"]


def _below100(n):
    if n < 20:
        return _U[n]
    if n < 70:
        t, u = divmod(n, 10)
        return _T[t] + ("" if u == 0 else " et un" if u == 1 else "-" + _U[u])
    if n < 80:
        r = n - 60
        return "soixante" + (" et " if r == 11 else "-") + _U[r]
    return "quatre-vingts" if n == 80 else "quatre-vingt-" + _U[n - 80]


def _below1000(n, final=True):
    if n < 100:
        return "quatre-vingt" if (n == 80 and not final) else _below100(n)
    c, r = divmod(n, 100)
    head = "cent" if c == 1 else _U[c] + " cent"
    if r == 0:
        return head + ("s" if c > 1 and final else "")
    return head + " " + _below100(r)


def words(n):
    n = int(n)
    if n == 0:
        return "zéro"
    if n >= 10 ** 9:
        return str(n)
    m, t, r = n // 10 ** 6, (n // 1000) % 1000, n % 1000
    out = []
    if m:
        out.append("un million" if m == 1 else _below1000(m) + " millions")
    if t:
        out.append("mille" if t == 1 else _below1000(t, False) + " mille")
    if r:
        out.append(_below1000(r))
    return " ".join(out)


def amount_words(v):
    dh, c = int(v), int(round((v - int(v)) * 100))
    if c == 100:
        dh, c = dh + 1, 0
    s = words(dh) + (" de" if dh and dh % 10 ** 6 == 0 else "") + " dirham" + ("s" if dh > 1 else "")
    if c:
        s += " et " + words(c) + " centime" + ("s" if c > 1 else "")
    return s[0].upper() + s[1:]
# --- WORDS END


# ============================================================ Paiements
def paid_of(inv):
    if "paid" in inv:
        return inv["paid"]
    return inv["total"] if inv.get("status") == "Payée" else 0.0


def legacy_fix(iid):
    """Anciennes factures 'Payée' sans historique : crée le paiement correspondant."""
    inv = DB.invoices.find_one({"id": iid})
    if inv and "paid" not in inv and inv["status"] == "Payée" and not DB.payments.find_one({"invoice_id": iid}):
        DB.payments.insert_one({"id": next_id("payments"), "invoice_id": iid, "date": inv["date"],
                                "amount": inv["total"], "mode": "Autre", "note": "Reprise ancien statut"})
        DB.invoices.update_one({"id": iid}, {"$set": {"paid": inv["total"]}})


def recompute_payment(iid):
    inv = DB.invoices.find_one({"id": iid})
    paid = round(sum(p["amount"] for p in DB.payments.find({"invoice_id": iid})), 2)
    st = inv["status"]
    if inv["doctype"] == "FAC" and st in DOCS["FAC"]["statuses"]:
        st = "Payée" if paid >= inv["total"] - 0.005 else ("Partiellement payée" if paid > 0 else "Non payée")
    DB.invoices.update_one({"id": iid}, {"$set": {"paid": paid, "status": st}})


def add_payment(iid, amount, mode="Virement", date=None, note=""):
    DB.payments.insert_one({"id": next_id("payments"), "invoice_id": iid, "date": date or today(),
                            "amount": round(amount, 2), "mode": mode, "note": note})
    recompute_payment(iid)


def mark_paid(iid):
    legacy_fix(iid)
    inv = DB.invoices.find_one({"id": iid})
    rest = inv["total"] - paid_of(inv)
    if rest > 0.005:
        add_payment(iid, rest, "Solde", note="Solde de la facture")


# ============================================================ Stock
def move_stock(pid, qty, kind, reason="", doc=""):
    DB.products.update_one({"id": pid}, {"$inc": {"stock": qty}})
    DB.movements.insert_one({"id": next_id("movements"), "product_id": pid, "date": now(), "kind": kind,
                             "qty": qty, "reason": reason, "doc": doc})


def stock_sign(doctype, status):
    if doctype == "FAC":
        return -1
    if doctype == "BC" and status == "Reçu":
        return 1
    return 0


def linked_items(iid):
    return list(DB.items.find({"invoice_id": iid, "product_id": {"$ne": None}}, {"_id": 0}))


def unapply_stock(iid):
    inv = DB.invoices.find_one({"id": iid})
    if not inv or not inv.get("stock_applied"):
        return
    for it in linked_items(iid):
        move_stock(it["product_id"], -inv["stock_applied"] * it["qty"], "Annulation", "Retour de stock", inv["number"])
    DB.invoices.update_one({"id": iid}, {"$set": {"stock_applied": 0}})


def sync_stock(iid):
    inv = DB.invoices.find_one({"id": iid})
    if not inv:
        return
    want, have = stock_sign(inv["doctype"], inv["status"]), inv.get("stock_applied", 0)
    if want == have:
        return
    if have:
        unapply_stock(iid)
    if want:
        kind = "Sortie facture" if want < 0 else "Entrée réception"
        for it in linked_items(iid):
            move_stock(it["product_id"], want * it["qty"], kind, "", inv["number"])
        DB.invoices.update_one({"id": iid}, {"$set": {"stock_applied": want}})


PFIELDS = [("Référence *", "ref"), ("Désignation *", "name"), ("Catégorie", "category"), ("Marque", "brand"),
           ("Description", "description"), ("Unité", "unit"), ("Dimensions", "dimensions"), ("Poids", "weight"),
           ("Couleur / finition", "color"), ("Code-barres", "barcode"), ("Prix d'achat HT", "purchase_price"),
           ("Prix de vente HT", "sale_price"), ("TVA %", "vat"), ("Stock initial", "stock"),
           ("Stock minimum", "min_stock"), ("Emplacement", "location"), ("Fournisseur", "supplier"),
           ("Image (chemin)", "image"), ("Notes", "notes")]
PNUMS = ("purchase_price", "sale_price", "vat", "stock", "min_stock")
PDEF = {k: "" for _, k in PFIELDS}
PDEF.update({k: 0 for k in PNUMS})
PDEF.update({"unit": "pièce", "created": ""})


def load_products():
    return [{**PDEF, **d} for d in DB.products.find({}, {"_id": 0}).sort("name", ASCENDING)]


def get_product(pid):
    d = DB.products.find_one({"id": pid}, {"_id": 0})
    return {**PDEF, **d} if d else None


def product_status(p):
    if p["stock"] <= 0:
        return "Rupture"
    return "Stock bas" if p["stock"] <= p["min_stock"] else "OK"


def make_sortable(t):
    state = {}

    def key(v):
        s = str(v).replace(" ", "").replace("DH", "").replace("+", "")
        try:
            return (0, float(s))
        except ValueError:
            return (1, str(v).lower())

    for c in t["columns"]:
        def cb(col=c):
            rev = state.get(col, False)
            its = sorted(((key(t.set(k, col)), k) for k in t.get_children("")), reverse=rev)
            for i, (_, k) in enumerate(its):
                t.move(k, "", i)
            state[col] = not rev
        t.heading(c, command=cb)


# ============================================================ Application
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        if DB is None:
            messagebox.showerror("MongoDB", f"MongoDB non connecté.\n\n{DB_ERROR}\n\nVérifiez MONGODB_URI dans .env")
            raise SystemExit(1)
        init_db()
        self.title(f"UNIK SYSTEM | Invoice Manager {VERSION}")
        self.geometry("1400x900")
        self.minsize(1200, 760)
        self.items, self.edit_id, self.cur_pid = [], None, None
        self._style()
        self._shell()
        self.show_dashboard()
        self.splash()

    # ---------------------------------------------------- base UI
    def load_logo(self, w):
        try:
            img = Image.open(setting("logo") or DEFAULT_LOGO)
            return ctk.CTkImage(img, size=(w, int(w * img.height / img.width)))
        except Exception:
            return None

    def splash(self):
        s = ctk.CTkToplevel(self)
        s.overrideredirect(True)
        w, h = 580, 270
        s.geometry(f"{w}x{h}+{(s.winfo_screenwidth() - w) // 2}+{(s.winfo_screenheight() - h) // 2}")
        s.configure(fg_color="white")
        s.attributes("-topmost", True)
        ctk.CTkFrame(s, height=6, corner_radius=0, fg_color=RED).pack(fill="x")
        s._img = self.load_logo(520)
        if s._img:
            ctk.CTkLabel(s, image=s._img, text="").pack(pady=(44, 10))
        else:
            ctk.CTkLabel(s, text="UNIK SYSTEM", text_color=RED, font=ctk.CTkFont(size=34, weight="bold")).pack(pady=50)
        ctk.CTkLabel(s, text=f"INVOICE MANAGER {VERSION}", text_color=GRAY,
                     font=ctk.CTkFont(size=12, weight="bold")).pack()
        bar = ctk.CTkProgressBar(s, width=380, progress_color=RED)
        bar.set(0)
        bar.pack(pady=18)

        def step(v=0.0):
            if v >= 1:
                s.destroy()
                self.deiconify()
                return
            bar.set(v)
            s.after(35, lambda: step(v + 0.04))
        step()

    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("Treeview", rowheight=32, font=("Segoe UI", 10), background="white",
                    fieldbackground="white", borderwidth=0)
        s.configure("Treeview.Heading", background=GRAY, foreground="white",
                    font=("Segoe UI", 10, "bold"), relief="flat")
        s.map("Treeview", background=[("selected", RED)], foreground=[("selected", "white")])
        s.map("Treeview.Heading", background=[("active", DARK)])

    def _shell(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        sb = ctk.CTkFrame(self, width=245, corner_radius=0, fg_color=("white", "#262624"))
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        ctk.CTkFrame(sb, height=5, corner_radius=0, fg_color=RED).pack(fill="x")
        holder = ctk.CTkFrame(sb, fg_color="white", corner_radius=10)
        holder.pack(padx=12, pady=(22, 6), fill="x")
        self.logo_lbl = ctk.CTkLabel(holder, text="UNIK SYSTEM", text_color=RED,
                                     font=ctk.CTkFont(size=22, weight="bold"))
        self.logo_lbl.pack(padx=6, pady=10)
        self.refresh_logo()
        ctk.CTkLabel(sb, text=f"INVOICE MANAGER {VERSION}", text_color=GRAY,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(pady=(4, 16))
        self.nav = {}
        for key, label, cmd in [
            ("dash", "⌂   Tableau de bord", self.show_dashboard),
            ("DEV", "◫   Devis", lambda: self.show_docs("DEV")),
            ("FAC", "▤   Factures", lambda: self.show_docs("FAC")),
            ("BC", "⛟   Bons de commande", lambda: self.show_docs("BC")),
            ("stock", "▦   Stock produits", self.show_stock),
            ("clients", "☺   Clients / Fournisseurs", self.show_clients),
            ("set", "⚙   Paramètres", self.show_settings)]:
            b = ctk.CTkButton(sb, text=label, command=cmd, anchor="w", height=42, corner_radius=8,
                              fg_color="transparent", text_color=(DARK, "white"), hover_color=("#E9E9E6", "#3B3A37"))
            b.pack(fill="x", padx=14, pady=2)
            self.nav[key] = b
        ctk.CTkButton(sb, text="◐  Thème clair / sombre", command=self.toggle_theme,
                      fg_color=GRAY, hover_color=DARK).pack(side="bottom", fill="x", padx=14, pady=18)
        self.content = ctk.CTkFrame(self, fg_color=("#F4F4F2", "#1E1E1C"), corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.body = None

    def refresh_logo(self):
        self.logo_img = self.load_logo(195)
        if self.logo_img:
            self.logo_lbl.configure(image=self.logo_img, text="")

    def page(self, key, title, subtitle=""):
        for k, b in self.nav.items():
            b.configure(fg_color=RED if k == key else "transparent",
                        text_color="white" if k == key else (DARK, "white"),
                        hover_color=RED_D if k == key else ("#E9E9E6", "#3B3A37"))
        if self.body:
            self.body.destroy()
        self.body = ctk.CTkFrame(self.content, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=26, pady=20)
        ctk.CTkLabel(self.body, text=title, text_color=(DARK, "white"),
                     font=ctk.CTkFont(size=28, weight="bold")).pack(anchor="w")
        ctk.CTkFrame(self.body, height=3, width=60, fg_color=RED).pack(anchor="w", pady=(4, 6))
        if subtitle:
            ctk.CTkLabel(self.body, text=subtitle, text_color=GRAY).pack(anchor="w", pady=(0, 8))
        return self.body

    def toggle_theme(self):
        ctk.set_appearance_mode("Light" if ctk.get_appearance_mode() == "Dark" else "Dark")

    def btn(self, parent, text, cmd, kind="red", **kw):
        c = {"red": (RED, RED_D), "gray": (GRAY, DARK), "green": (GREEN, "#237A46")}[kind]
        return ctk.CTkButton(parent, text=text, command=cmd, fg_color=c[0], hover_color=c[1],
                             height=kw.pop("height", 38), **kw)

    def dialog(self, title, size):
        top = ctk.CTkToplevel(self)
        top.title(title)
        top.geometry(size)
        top.transient(self)
        top.after(150, top.grab_set)
        return top

    def stat_cards(self, parent, data):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 6))
        for label, val, col in data:
            c = ctk.CTkFrame(row, corner_radius=10)
            c.pack(side="left", fill="x", expand=True, padx=4)
            ctk.CTkFrame(c, height=4, corner_radius=0, fg_color=col).pack(fill="x")
            ctk.CTkLabel(c, text=label, text_color=GRAY).pack(anchor="w", padx=14, pady=(8, 0))
            ctk.CTkLabel(c, text=val, text_color=col, font=ctk.CTkFont(size=19, weight="bold")).pack(
                anchor="w", padx=14, pady=(0, 10))

    def selected(self, t, what="un document"):
        s = t.selection()
        if not s:
            messagebox.showinfo("Sélection", f"Sélectionnez d'abord {what}.")
            return None
        return int(s[0])

    def doc_tree(self, parent, rows, height=12, party="Client", widths=(160, 100, 300, 130, 130, 170)):
        f = ctk.CTkFrame(parent)
        f.pack(fill="both", expand=True, pady=6)
        cols = ("number", "date", "client", "total", "reste", "status")
        t = ttk.Treeview(f, columns=cols, show="headings", height=height)
        for c, l, w in zip(cols, ["N° Document", "Date", party, "Total TTC", "Reste", "Statut"], widths):
            t.heading(c, text=l)
            t.column(c, width=w, anchor="e" if c in ("total", "reste") else "w")
        for st, col in STATUS_COLORS.items():
            t.tag_configure(st, foreground=col)
        t.tag_configure("late", foreground=RED_D)
        sc = ttk.Scrollbar(f, command=t.yview)
        t.configure(yscrollcommand=sc.set)
        sc.pack(side="right", fill="y", pady=8)
        t.pack(fill="both", expand=True, padx=(8, 0), pady=8)
        make_sortable(t)
        self.fill_tree(t, rows)
        t.bind("<Double-1>", lambda e: self.open_pdf(t))
        return t

    def fill_tree(self, t, rows):
        t.delete(*t.get_children())
        td = today()
        for r in rows:
            st, tags, reste = r["status"], [r["status"]], ""
            if r["doctype"] == "FAC":
                rest = r["total"] - paid_of(r)
                reste = fmt(max(rest, 0))
                if rest > 0.005 and r.get("due") and r["due"] < td:
                    st, tags = st + "  ⚠ retard", ["late"]
            t.insert("", "end", iid=str(r["id"]), tags=tags,
                     values=(r["number"], r["date"], r["client"], fmt(r["total"]), reste, st))

    # ---------------------------------------------------- Dashboard
    def show_dashboard(self):
        b = self.page("dash", "Tableau de bord", "Vue d'ensemble de votre activité.")
        rows = list(DB.invoices.find({"doctype": "FAC"}, {"_id": 0}).sort("id", DESCENDING))
        n_dev = DB.invoices.count_documents({"doctype": "DEV", "status": {"$in": ["Brouillon", "Envoyé"]}})
        n_bc = DB.invoices.count_documents({"doctype": "BC", "status": {"$in": ["Brouillon", "Envoyé", "Confirmé"]}})
        prods = load_products()
        td, year = today(), str(datetime.date.today().year)
        total = sum(r["total"] for r in rows)
        paid = sum(paid_of(r) for r in rows)
        unpaid = [r for r in rows if r["total"] - paid_of(r) > 0.005]
        late = [r for r in unpaid if r.get("due") and r["due"] < td]
        low = [p for p in prods if p["stock"] <= p["min_stock"]]
        self.stat_cards(b, [("Factures", str(len(rows)), GRAY), ("Chiffre d'affaires", fmt(total), DARK),
                            ("Encaissé", fmt(paid), GREEN), ("Reste à encaisser", fmt(total - paid), RED),
                            ("En retard", str(len(late)), RED_D)])
        self.stat_cards(b, [("Devis en cours", str(n_dev), ORANGE), ("Bons de commande en cours", str(n_bc), ORANGE),
                            ("Produits", str(len(prods)), GRAY),
                            ("Valeur du stock (achat)", fmt(sum(p["stock"] * p["purchase_price"] for p in prods)), DARK),
                            ("Alertes stock", str(len(low)), RED)])
        months = [sum(r["total"] for r in rows if r["date"].startswith(f"{year}-{m:02d}")) for m in range(1, 13)]
        ch = ctk.CTkFrame(b)
        ch.pack(fill="x", pady=6)
        ctk.CTkLabel(ch, text=f"Chiffre d'affaires mensuel {year}", text_color=(DARK, "white"),
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=14, pady=(8, 0))
        cv = tk.Canvas(ch, height=120, highlightthickness=0,
                       bg="white" if ctk.get_appearance_mode() == "Light" else "#2B2B2B")
        cv.pack(fill="x", padx=14, pady=6)

        def draw(_=None):
            cv.delete("all")
            w, h, mx = cv.winfo_width(), 120, max(months) or 1
            step = w / 12
            for i, v in enumerate(months):
                bh = (h - 40) * v / mx
                x = i * step + step * 0.2
                cv.create_rectangle(x, h - 22 - bh, x + step * 0.6, h - 22, fill=RED if v else "#D8D8D4", width=0)
                cv.create_text(x + step * 0.3, h - 9, text="JFMAMJJASOND"[i], fill=GRAY)
                if v:
                    cv.create_text(x + step * 0.3, h - 30 - bh, text=f"{v:,.0f}".replace(",", " "),
                                   fill=GRAY, font=("Segoe UI", 8))
        cv.bind("<Configure>", draw)
        qa = ctk.CTkFrame(b, fg_color="transparent")
        qa.pack(anchor="w", pady=2)
        self.btn(qa, "＋  Nouvelle facture", lambda: self.doc_form("FAC"), width=170).pack(side="left", padx=(0, 6))
        self.btn(qa, "＋  Nouveau devis", lambda: self.doc_form("DEV"), "gray", width=170).pack(side="left", padx=6)
        self.btn(qa, "＋  Nouveau bon de commande", lambda: self.doc_form("BC"), "gray", width=220).pack(side="left", padx=6)

        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(fill="both", expand=True)
        row.grid_columnconfigure(0, weight=3)
        row.grid_columnconfigure(1, weight=2)
        row.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right = ctk.CTkFrame(row, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(left, text="Factures à encaisser", text_color=(DARK, "white"),
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
        unpaid.sort(key=lambda r: r.get("due") or "9999")
        self.doc_tree(left, unpaid[:8], 4, widths=(115, 85, 170, 105, 105, 120))
        ctk.CTkLabel(right, text="Alertes stock", text_color=(DARK, "white"),
                     font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
        f = ctk.CTkFrame(right)
        f.pack(fill="both", expand=True, pady=6)
        t = ttk.Treeview(f, columns=("ref", "name", "stock", "min"), show="headings", height=4)
        for c, l, w, a in zip(("ref", "name", "stock", "min"), ["Réf", "Produit", "Stock", "Min"],
                              [90, 200, 60, 60], ["w", "w", "e", "e"]):
            t.heading(c, text=l)
            t.column(c, width=w, anchor=a)
        t.tag_configure("Rupture", foreground=RED)
        t.tag_configure("Stock bas", foreground=ORANGE)
        for p in low[:20]:
            t.insert("", "end", tags=(product_status(p),),
                     values=(p["ref"], p["name"], f"{p['stock']:g}", f"{p['min_stock']:g}"))
        t.pack(fill="both", expand=True, padx=8, pady=8)

    # ---------------------------------------------------- Liste documents
    def show_docs(self, dt):
        cfg = DOCS[dt]
        b = self.page(dt, cfg["plural"], "Double-clic : ouvrir le PDF  •  clic sur un en-tête : trier.")
        rows = list(DB.invoices.find({"doctype": dt}, {"_id": 0}).sort("id", DESCENDING))
        tot = sum(r["total"] for r in rows)
        cards = [("Documents", str(len(rows)), GRAY), ("Montant total TTC", fmt(tot), DARK)]
        if dt == "FAC":
            pd = sum(paid_of(r) for r in rows)
            cards += [("Encaissé", fmt(pd), GREEN), ("Reste à encaisser", fmt(tot - pd), RED)]
        self.stat_cards(b, cards)
        bar = ctk.CTkFrame(b, fg_color="transparent")
        bar.pack(fill="x")
        search = ctk.CTkEntry(bar, placeholder_text="🔍 Numéro ou nom...", width=300, height=38)
        search.pack(side="left")
        flt = ctk.CTkComboBox(bar, values=["Tous"] + cfg["statuses"], width=190, height=38)
        flt.set("Tous")
        flt.pack(side="left", padx=8)
        self.btn(bar, f"＋  Nouveau ({cfg['label'].lower()})", lambda: self.doc_form(dt), width=230).pack(side="right")
        t = self.doc_tree(b, rows, 10, cfg["party"])

        def refresh(*_):
            q, s = search.get().lower(), flt.get()
            self.fill_tree(t, [r for r in rows if (q in r["number"].lower() or q in r["client"].lower())
                               and (s == "Tous" or r["status"] == s)])
        search.bind("<KeyRelease>", refresh)
        flt.configure(command=refresh)

        def set_status(status):
            i = self.selected(t)
            if i:
                unapply_stock(i)
                DB.invoices.update_one({"id": i}, {"$set": {"status": status}})
                sync_stock(i)
                self.show_docs(dt)

        def delete():
            i = self.selected(t)
            if i and messagebox.askyesno("Supprimer", "Supprimer définitivement ce document ?\n(Le stock lié sera restitué.)"):
                unapply_stock(i)
                DB.invoices.delete_one({"id": i})
                DB.items.delete_many({"invoice_id": i})
                DB.payments.delete_many({"invoice_id": i})
                self.show_docs(dt)

        def export():
            p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
            if p:
                with open(p, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(["Numéro", "Date", cfg["due_label"], cfg["party"], "HT", "TVA", "Remise %", "TTC", "Réglé", "Statut"])
                    for r in rows:
                        w.writerow([r["number"], r["date"], r.get("due", ""), r["client"], r["subtotal"], r["tax"],
                                    r["discount"], r["total"], paid_of(r) if dt == "FAC" else "", r["status"]])
                messagebox.showinfo("Export", "Export CSV terminé.")

        def mail():
            i = self.selected(t)
            if not i:
                return
            inv = DB.invoices.find_one({"id": i})
            path = self.pdf_path(inv)
            try:
                self.make_pdf(i, path)
            except Exception as e:
                return messagebox.showerror("Erreur PDF", str(e))
            subj = f"{cfg['label']} {inv['number']} - {setting('company')}"
            body = (f"Bonjour,\n\nVeuillez trouver ci-joint {cfg['label'].lower()} n° {inv['number']} "
                    f"d'un montant de {fmt(inv['total'])}.\n\nCordialement,\n{setting('company')}")
            webbrowser.open(f"mailto:{inv.get('email', '')}?subject={urllib.parse.quote(subj)}&body={urllib.parse.quote(body)}")
            messagebox.showinfo("Email", f"Joignez le PDF à votre message :\n{path}")

        def edit():
            i = self.selected(t)
            if i:
                self.doc_form(dt, edit_id=i)

        def dup():
            i = self.selected(t)
            if i:
                self.doc_form(dt, dup_id=i)

        def convert():
            i = self.selected(t)
            if i:
                self.doc_form("FAC", from_doc=i)

        def settle():
            i = self.selected(t)
            if i:
                mark_paid(i)
                self.show_docs(dt)

        def pay():
            i = self.selected(t)
            if i:
                self.payments_dialog(i)

        act = ctk.CTkFrame(b, fg_color="transparent")
        act.pack(fill="x", pady=4)
        self.btn(act, "Ouvrir PDF", lambda: self.open_pdf(t), width=112).pack(side="left", padx=3)
        self.btn(act, "Modifier", edit, "gray", width=112).pack(side="left", padx=3)
        self.btn(act, "Dupliquer", dup, "gray", width=112).pack(side="left", padx=3)
        if dt == "FAC":
            self.btn(act, "💳 Paiements", pay, "green", width=125).pack(side="left", padx=3)
            self.btn(act, "✔ Solder", settle, "green", width=112).pack(side="left", padx=3)
        if dt == "DEV":
            self.btn(act, "→ Convertir en facture", convert, "green", width=190).pack(side="left", padx=3)
        if dt == "BC":
            self.btn(act, "📥 Marquer reçu (+stock)", lambda: set_status("Reçu"), "green", width=190).pack(side="left", padx=3)
        self.btn(act, "✉ Email", mail, "gray", width=100).pack(side="left", padx=3)
        self.btn(act, "CSV", export, "gray", width=80).pack(side="left", padx=3)
        self.btn(act, "Supprimer", delete, width=112).pack(side="right", padx=3)
        if dt != "FAC":
            st = ctk.CTkFrame(b, fg_color="transparent")
            st.pack(fill="x", pady=2)
            ctk.CTkLabel(st, text="Changer le statut :", text_color=GRAY).pack(side="left", padx=(3, 8))
            sc = ctk.CTkComboBox(st, values=cfg["statuses"], width=190)
            sc.set(cfg["statuses"][0])
            sc.pack(side="left")
            self.btn(st, "Appliquer", lambda: set_status(sc.get()), "gray", width=100).pack(side="left", padx=8)

    def pdf_path(self, inv):
        return os.path.join(PDF_DIR, f"{DOCS[inv['doctype']]['file']}_{inv['number']}.pdf")

    def open_pdf(self, t):
        i = self.selected(t)
        if not i:
            return
        inv = DB.invoices.find_one({"id": i})
        path = self.pdf_path(inv)
        try:
            self.make_pdf(i, path)
            open_file(path)
        except Exception as e:
            messagebox.showerror("Erreur PDF", str(e))

    # ---------------------------------------------------- Paiements
    def payments_dialog(self, iid):
        legacy_fix(iid)
        number = DB.invoices.find_one({"id": iid})["number"]
        top = self.dialog(f"Paiements — {number}", "680x560")

        def close():
            top.destroy()
            self.show_docs("FAC")
        top.protocol("WM_DELETE_WINDOW", close)
        info = ctk.CTkLabel(top, text="", text_color=RED, font=ctk.CTkFont(size=15, weight="bold"))
        info.pack(pady=(16, 6))
        cols = ("date", "mode", "amount", "note")
        t = ttk.Treeview(top, columns=cols, show="headings", height=8)
        for c, l, w in zip(cols, ["Date", "Mode", "Montant", "Note"], [110, 110, 130, 260]):
            t.heading(c, text=l)
            t.column(c, width=w, anchor="e" if c == "amount" else "w")
        t.pack(fill="both", expand=True, padx=14, pady=6)
        f = ctk.CTkFrame(top)
        f.pack(fill="x", padx=14, pady=6)
        amt = ctk.CTkEntry(f, width=110, placeholder_text="Montant")
        amt.grid(row=0, column=0, padx=6, pady=8)
        mode = ctk.CTkComboBox(f, values=MODES, width=120)
        mode.grid(row=0, column=1, padx=6)
        date = ctk.CTkEntry(f, width=110)
        date.insert(0, today())
        date.grid(row=0, column=2, padx=6)
        note = ctk.CTkEntry(f, width=170, placeholder_text="Note")
        note.grid(row=0, column=3, padx=6)

        def reload():
            inv = DB.invoices.find_one({"id": iid})
            paid = paid_of(inv)
            info.configure(text=f"Total {fmt(inv['total'])}  |  Réglé {fmt(paid)}  |  Reste {fmt(max(inv['total'] - paid, 0))}")
            t.delete(*t.get_children())
            for p in DB.payments.find({"invoice_id": iid}).sort("date", ASCENDING):
                t.insert("", "end", iid=str(p["id"]), values=(p["date"], p["mode"], fmt(p["amount"]), p.get("note", "")))
            amt.delete(0, "end")
            amt.insert(0, f"{max(inv['total'] - paid, 0):.2f}")

        def add():
            try:
                a = num(amt.get())
                datetime.date.fromisoformat(date.get().strip())
                if a <= 0:
                    raise ValueError
            except ValueError:
                return messagebox.showerror("Invalide", "Montant > 0 et date AAAA-MM-JJ requis.", parent=top)
            add_payment(iid, a, mode.get(), date.get().strip(), note.get().strip())
            note.delete(0, "end")
            reload()

        def remove():
            s = t.selection()
            if s:
                DB.payments.delete_one({"id": int(s[0])})
                recompute_payment(iid)
                reload()
        row = ctk.CTkFrame(top, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=10)
        self.btn(row, "Ajouter le paiement", add, "green", width=170).pack(side="left")
        self.btn(row, "Supprimer la ligne", remove, "gray", width=160).pack(side="left", padx=8)
        self.btn(row, "Fermer", close, width=100).pack(side="right")
        reload()

    # ---------------------------------------------------- Formulaire document
    def doc_form(self, dt, edit_id=None, dup_id=None, from_doc=None):
        cfg = DOCS[dt]
        self.doctype, self.edit_id, self.from_doc, self.items, self.cur_pid = dt, edit_id, from_doc, [], None
        src = edit_id or dup_id or from_doc
        inv = None
        if src:
            inv = DB.invoices.find_one({"id": src}, {"_id": 0})
            self.items = list(DB.items.find({"invoice_id": src}, {"_id": 0}))
        self.cur_status = inv["status"] if edit_id else cfg["statuses"][0]
        self.pmap = {f"{p['ref']} — {p['name']}": p for p in load_products()}
        ptype = "Fournisseur" if dt == "BC" else "Client"
        self.tmap = {c["name"]: c for c in DB.clients.find({"type": ptype}, {"_id": 0}).sort("name", ASCENDING)}
        if edit_id:
            title, sub = f"Modifier : {inv['number']}", "Modifiez les informations puis enregistrez."
        elif from_doc:
            title, sub = "Nouvelle facture", f"Créée à partir du devis {inv['number']}"
        else:
            title, sub = f"Nouveau : {cfg['label'].lower()}", "Tiers, articles, remise et statut."
        b = self.page(dt, title, sub)
        tp = ctk.CTkFrame(b)
        tp.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(tp, text=f"{ptype} enregistré :").pack(side="left", padx=10, pady=8)
        tcombo = ctk.CTkComboBox(tp, values=["— Choisir —"] + list(self.tmap), width=380, command=self.pick_tier)
        tcombo.set("— Choisir —")
        tcombo.pack(side="left", pady=8)
        form = ctk.CTkFrame(b)
        form.pack(fill="x", pady=4)
        self.f = {}
        specs = [(cfg["party"] + " *", "client"), ("ICE", "ice"), ("Adresse", "address"), ("Téléphone", "phone"),
                 ("Email", "email"), (cfg["due_label"], "due")]
        if dt != "FAC":
            specs.append(("Statut", "status"))
        specs += [("Remise %", "discount"), ("Notes", "notes")]
        for i, (lab, key) in enumerate(specs):
            r, c = divmod(i, 2)
            c *= 2
            ctk.CTkLabel(form, text=lab).grid(row=r, column=c, padx=10, pady=4, sticky="w")
            if key == "status":
                w = ctk.CTkComboBox(form, values=cfg["statuses"], width=280)
                w.set(self.cur_status)
            else:
                w = ctk.CTkEntry(form, width=280)
                val = inv.get(key, "") if inv else ""
                if key == "due" and not edit_id:
                    val = (datetime.date.today() + datetime.timedelta(days=cfg["days"])).isoformat()
                if key == "discount":
                    val = inv.get("discount", 0) if inv else 0
                    w.bind("<KeyRelease>", lambda e: self.refresh_items())
                w.insert(0, str(val if val is not None else ""))
            w.grid(row=r, column=c + 1, padx=10, pady=4, sticky="ew")
            self.f[key] = w
        pf = ctk.CTkFrame(b)
        pf.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(pf, text="Produit du stock :").pack(side="left", padx=10, pady=8)
        self.pcombo = ctk.CTkComboBox(pf, values=[LIBRE] + list(self.pmap), width=520, command=self.pick_product)
        self.pcombo.set(LIBRE)
        self.pcombo.pack(side="left", pady=8)
        self.stock_hint = ctk.CTkLabel(pf, text="", text_color=GRAY)
        self.stock_hint.pack(side="left", padx=12)
        line = ctk.CTkFrame(b)
        line.pack(fill="x", pady=6)
        self.ie = []
        for i, (lab, w) in enumerate([("Référence", 110), ("Désignation", 300), ("Quantité", 80), ("Prix HT", 110), ("TVA %", 80)]):
            ctk.CTkLabel(line, text=lab).grid(row=0, column=i, padx=4)
            e = ctk.CTkEntry(line, width=w)
            e.grid(row=1, column=i, padx=4, pady=6)
            self.ie.append(e)
        self.ie[2].insert(0, "1")
        self.ie[4].insert(0, "20")
        self.btn(line, "Ajouter", self.add_item, width=100).grid(row=1, column=5, padx=6)
        ctk.CTkLabel(line, text="(double-clic sur une ligne pour la modifier)", text_color=GRAY).grid(row=1, column=6, padx=6)
        tf = ctk.CTkFrame(b)
        tf.pack(fill="both", expand=True)
        cols = ("ref", "desc", "qty", "price", "vat", "ttc")
        self.it = ttk.Treeview(tf, columns=cols, show="headings", height=5)
        for c, l, w in zip(cols, ["Réf", "Désignation", "Qté", "Prix HT", "TVA %", "Total TTC"], [110, 380, 80, 120, 80, 140]):
            self.it.heading(c, text=l)
            self.it.column(c, width=w, anchor="w" if c in ("ref", "desc") else "e")
        self.it.pack(fill="both", expand=True, padx=8, pady=8)
        self.it.bind("<Double-1>", self.edit_item)
        self.total_lbl = ctk.CTkLabel(b, text="", font=ctk.CTkFont(size=16, weight="bold"), text_color=RED)
        self.total_lbl.pack(anchor="e", pady=4)
        bt = ctk.CTkFrame(b, fg_color="transparent")
        bt.pack(fill="x")
        self.btn(bt, "Supprimer la ligne", self.del_item, "gray").pack(side="left")
        self.btn(bt, "Enregistrer et générer le PDF", self.save_doc, width=240).pack(side="right", padx=4)
        self.btn(bt, "Annuler", lambda: self.show_docs(dt), "gray").pack(side="right", padx=4)
        self.refresh_items()

    def pick_tier(self, name):
        t = self.tmap.get(name)
        if not t:
            return
        for k, s in (("client", "name"), ("ice", "ice"), ("address", "address"), ("phone", "phone"), ("email", "email")):
            self.f[k].delete(0, "end")
            self.f[k].insert(0, t.get(s, "") or "")

    def pick_product(self, choice):
        p = self.pmap.get(choice)
        self.cur_pid = p["id"] if p else None
        for i in (0, 1, 3):
            self.ie[i].delete(0, "end")
        if not p:
            self.stock_hint.configure(text="")
            return
        name = p["name"] + (f" - {p['dimensions']}" if p.get("dimensions") else "")
        price = p["purchase_price"] if self.doctype == "BC" else p["sale_price"]
        self.ie[0].insert(0, p["ref"])
        self.ie[1].insert(0, name)
        self.ie[3].insert(0, f"{price:.2f}")
        self.ie[4].delete(0, "end")
        self.ie[4].insert(0, f"{p['vat']:g}")
        self.stock_hint.configure(text=f"Stock : {p['stock']:g} {p['unit']}",
                                  text_color=RED if p["stock"] <= p["min_stock"] else GREEN)

    def disc(self):
        try:
            return min(max(float(self.f["discount"].get().replace(",", ".") or 0), 0), 100)
        except ValueError:
            return 0.0

    def add_item(self):
        try:
            ref, desc = self.ie[0].get().strip(), self.ie[1].get().strip()
            q, p, v = (num(self.ie[i].get()) for i in (2, 3, 4))
            if not desc or q <= 0 or p < 0 or v < 0:
                raise ValueError
        except ValueError:
            return messagebox.showerror("Données invalides", "Vérifiez désignation, quantité, prix et TVA.")
        pid = self.cur_pid
        if pid and self.doctype == "FAC":
            r = get_product(pid)
            if r and q > r["stock"] and not messagebox.askyesno(
                    "Stock insuffisant", f"Stock disponible pour « {r['name']} » : {r['stock']:g}.\nAjouter quand même ?"):
                return
        self.items.append({"ref": ref, "description": desc, "qty": q, "price": p, "vat": v, "product_id": pid})
        for i in (0, 1, 3):
            self.ie[i].delete(0, "end")
        self.pcombo.set(LIBRE)
        self.cur_pid = None
        self.stock_hint.configure(text="")
        self.refresh_items()

    def edit_item(self, _=None):
        s = self.it.selection()
        if not s:
            return
        it = self.items.pop(self.it.index(s[0]))
        for e, v in zip(self.ie, [it.get("ref", ""), it["description"], f"{it['qty']:g}", f"{it['price']:.2f}", f"{it['vat']:g}"]):
            e.delete(0, "end")
            e.insert(0, v)
        self.cur_pid = it.get("product_id")
        self.refresh_items()

    def del_item(self):
        s = self.it.selection()
        if s:
            self.items.pop(self.it.index(s[0]))
            self.refresh_items()

    def refresh_items(self):
        self.it.delete(*self.it.get_children())
        for i in self.items:
            self.it.insert("", "end", values=(i.get("ref", ""), i["description"], f"{i['qty']:g}", f"{i['price']:.2f}",
                                              f"{i['vat']:g}", fmt(i["qty"] * i["price"] * (1 + i["vat"] / 100))))
        ht, rem, net, tva, ttc = calc(self.items, self.disc())
        self.total_lbl.configure(text=f"HT {fmt(ht)}  |  Remise {fmt(rem)}  |  TVA {fmt(tva)}  |  TTC {fmt(ttc)}")

    def save_doc(self):
        dt = self.doctype
        cfg = DOCS[dt]
        client = self.f["client"].get().strip()
        if not client or not self.items:
            return messagebox.showwarning("Informations manquantes", f"{cfg['party']} et au moins un article sont obligatoires.")
        d = self.disc()
        ht, rem, net, tva, ttc = calc(self.items, d)
        g = lambda k: self.f[k].get().strip()
        data = {"due": g("due"), "client": client, "address": g("address"), "phone": g("phone"), "email": g("email"),
                "ice": g("ice"), "status": self.f["status"].get() if "status" in self.f else self.cur_status,
                "subtotal": net, "tax": tva, "discount": d, "total": ttc, "notes": g("notes")}
        try:
            if self.edit_id:
                iid = self.edit_id
                legacy_fix(iid)
                unapply_stock(iid)
                DB.invoices.update_one({"id": iid}, {"$set": data})
                DB.items.delete_many({"invoice_id": iid})
                number = DB.invoices.find_one({"id": iid})["number"]
            else:
                number, iid = next_number(cfg["prefix"]), next_id("invoices")
                DB.invoices.insert_one({"id": iid, "number": number, "date": today(), "doctype": dt, **data,
                                        "stock_applied": 0, "paid": 0})
            DB.items.insert_many([{"invoice_id": iid, "ref": x.get("ref", ""), "description": x["description"],
                                   "qty": x["qty"], "price": x["price"], "vat": x["vat"],
                                   "product_id": x.get("product_id")} for x in self.items])
        except DuplicateKeyError:
            return messagebox.showerror("Numéro existant", "Conflit de numérotation, réessayez.")
        if self.from_doc:
            DB.invoices.update_one({"id": self.from_doc}, {"$set": {"status": "Converti"}})
        sync_stock(iid)
        if dt == "FAC":
            recompute_payment(iid)
        ptype = "Fournisseur" if dt == "BC" else "Client"
        if not DB.clients.find_one({"name": client, "type": ptype}):
            DB.clients.insert_one({"id": next_id("clients"), "name": client, "type": ptype, "ice": data["ice"],
                                   "address": data["address"], "phone": data["phone"], "email": data["email"],
                                   "notes": "", "created": today()})
        inv = DB.invoices.find_one({"id": iid})
        path = self.pdf_path(inv)
        try:
            self.make_pdf(iid, path)
        except Exception as e:
            return messagebox.showerror("Erreur PDF", str(e))
        if messagebox.askyesno("Document enregistré", f"{cfg['label']} {number} enregistré.\nOuvrir le PDF ?"):
            open_file(path)
        self.show_docs(dt)

    # ---------------------------------------------------- PDF
    def make_pdf(self, invoice_id, path):
        inv = DB.invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not inv:
            raise ValueError("Document introuvable.")
        items = list(DB.items.find({"invoice_id": invoice_id}, {"_id": 0}))
        S = {r["key"]: r["value"] for r in DB.settings.find({})}
        cfg = DOCS[inv["doctype"]]
        W, H = A4
        M = 16 * mm
        red, gray, dark = colors.HexColor(RED), colors.HexColor(GRAY), colors.HexColor(DARK)
        light = colors.HexColor("#F4F4F2")
        pdf = canvas.Canvas(path, pagesize=A4)
        pdf.setTitle(f"{cfg['label']} {inv['number']}")

        def header(first):
            pdf.setFillColor(red)
            pdf.rect(0, H - 6 * mm, W, 6 * mm, fill=1, stroke=0)
            lg = S.get("logo", "")
            if lg and os.path.isfile(lg):
                try:
                    iw, ih = ImageReader(lg).getSize()
                    lw = 78 * mm
                    pdf.drawImage(lg, M, H - 12 * mm - lw * ih / iw, width=lw, height=lw * ih / iw, mask="auto")
                except Exception:
                    pass
            pdf.setFillColor(red)
            pdf.setFont("Helvetica-Bold", 26 if len(cfg["title"]) < 10 else 19)
            pdf.drawRightString(W - M, H - 22 * mm, cfg["title"])
            pdf.setFillColor(gray)
            pdf.setFont("Helvetica", 9)
            pdf.drawRightString(W - M, H - 28 * mm, f"N° {inv['number']}")
            if first:
                pdf.setFillColor(dark)
                pdf.setFont("Helvetica-Bold", 10)
                pdf.drawString(M, H - 34 * mm, S.get("company", ""))
                pdf.setFont("Helvetica", 8.5)
                pdf.setFillColor(gray)
                pdf.drawString(M, H - 39 * mm, S.get("address", "")[:95])
                pdf.drawString(M, H - 44 * mm, f"Tél : {S.get('phone', '')}   |   {S.get('email', '')}")

        def footer():
            pdf.setStrokeColor(red)
            pdf.setLineWidth(1.2)
            pdf.line(M, 20 * mm, W - M, 20 * mm)
            pdf.setFillColor(gray)
            pdf.setFont("Helvetica", 7.5)
            legal = "   |   ".join(f"{k.upper()} : {S[k]}" for k in ("ice", "if", "rc", "tp") if S.get(k))
            pdf.drawCentredString(W / 2, 15 * mm, legal or S.get("company", ""))
            pdf.drawCentredString(W / 2, 10.5 * mm, f"{S.get('company', '')} — Smart solution for smart covering")
            pdf.drawRightString(W - M, 6 * mm, f"Page {pdf.getPageNumber()}")

        def table_head(y):
            pdf.setFillColor(gray)
            pdf.rect(M, y - 9 * mm, W - 2 * M, 9 * mm, fill=1, stroke=0)
            pdf.setFillColor(colors.white)
            pdf.setFont("Helvetica-Bold", 8.5)
            pdf.drawString(M + 2 * mm, y - 6 * mm, "RÉF.")
            pdf.drawString(M + 24 * mm, y - 6 * mm, "DÉSIGNATION")
            for x, t in ((M + 111 * mm, "QTÉ"), (M + 135 * mm, "P.U. HT"), (M + 148 * mm, "TVA"), (W - M - 2 * mm, "TOTAL HT")):
                pdf.drawRightString(x, y - 6 * mm, t)
            return y - 9 * mm

        header(True)
        y = H - 56 * mm
        pdf.setFillColor(light)
        pdf.rect(M, y - 27 * mm, 86 * mm, 27 * mm, fill=1, stroke=0)
        pdf.setFillColor(red)
        pdf.rect(M, y - 27 * mm, 1.8 * mm, 27 * mm, fill=1, stroke=0)
        pdf.setFillColor(gray)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(M + 6 * mm, y - 6 * mm, cfg["party_pdf"])
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(M + 6 * mm, y - 12 * mm, inv["client"][:38])
        pdf.setFont("Helvetica", 8.5)
        pdf.drawString(M + 6 * mm, y - 17 * mm, (inv.get("address") or "")[:52])
        pdf.drawString(M + 6 * mm, y - 21.5 * mm, f"{inv.get('phone') or ''}  {inv.get('email') or ''}"[:52])
        if inv.get("ice"):
            pdf.drawString(M + 6 * mm, y - 25.5 * mm, f"ICE : {inv['ice']}")
        x0 = W - M - 78 * mm
        for k, (lab, val) in enumerate([("Date", inv["date"]), (cfg["due_label"], inv.get("due") or "-"), ("Statut", inv["status"])]):
            pdf.setFillColor(gray)
            pdf.setFont("Helvetica", 9)
            pdf.drawString(x0, y - 7 * mm - k * 8 * mm, lab)
            pdf.setFillColor(colors.HexColor(STATUS_COLORS.get(val, GRAY)) if lab == "Statut" else dark)
            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawRightString(W - M, y - 7 * mm - k * 8 * mm, str(val))

        y = table_head(y - 36 * mm)
        for n, it in enumerate(items):
            lines = simpleSplit(str(it.get("description") or ""), "Helvetica", 8.5, 76 * mm) or [""]
            rh = max(8 * mm, (len(lines) * 3.8 + 4.4) * mm)
            if y - rh < 28 * mm:
                footer()
                pdf.showPage()
                header(False)
                y = table_head(H - 40 * mm)
            if n % 2 == 0:
                pdf.setFillColor(light)
                pdf.rect(M, y - rh, W - 2 * M, rh, fill=1, stroke=0)
            pdf.setFillColor(dark)
            pdf.setFont("Helvetica", 8.5)
            pdf.drawString(M + 2 * mm, y - 5.5 * mm, str(it.get("ref") or "-")[:12])
            for k, ln in enumerate(lines):
                pdf.drawString(M + 24 * mm, y - 5.5 * mm - k * 3.8 * mm, ln)
            pdf.drawRightString(M + 111 * mm, y - 5.5 * mm, f"{it['qty']:g}")
            pdf.drawRightString(M + 135 * mm, y - 5.5 * mm, f"{it['price']:,.2f}".replace(",", " "))
            pdf.drawRightString(M + 148 * mm, y - 5.5 * mm, f"{it['vat']:g}%")
            pdf.drawRightString(W - M - 2 * mm, y - 5.5 * mm, f"{it['qty'] * it['price']:,.2f}".replace(",", " "))
            y -= rh
        pdf.setStrokeColor(colors.lightgrey)
        pdf.setLineWidth(0.6)
        pdf.line(M, y, W - M, y)
        if y < 112 * mm:  # pas assez de place pour totaux + signatures
            footer()
            pdf.showPage()
            header(False)
            y = H - 45 * mm

        d = inv.get("discount") or 0
        ht, rem, net, tva, ttc = calc(items, d)
        top_y = y - 8 * mm
        # ---- colonne gauche : montant en lettres, paiement / conditions
        ly = top_y
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica-Bold", 8.5)
        pdf.drawString(M, ly, cfg["arret"])
        ly -= 4.5 * mm
        pdf.setFont("Helvetica-Oblique", 8.5)
        for ln in simpleSplit(amount_words(ttc), "Helvetica-Oblique", 8.5, 98 * mm):
            pdf.drawString(M, ly, ln)
            ly -= 4 * mm
        ly -= 3 * mm
        pdf.setFont("Helvetica-Bold", 8.5)
        pdf.drawString(M, ly, "Informations de paiement" if inv["doctype"] == "FAC" else "Conditions")
        ly -= 4.5 * mm
        pdf.setFont("Helvetica", 8)
        pdf.setFillColor(gray)
        cond = {"FAC": S.get("conditions", ""), "DEV": S.get("conditions_devis", ""), "BC": S.get("conditions_bc", "")}[inv["doctype"]]
        if inv["doctype"] == "FAC" and S.get("rib"):
            pdf.drawString(M, ly, f"RIB : {S['rib']}"[:70])
            ly -= 4.5 * mm
        for ln in simpleSplit(cond, "Helvetica", 8, 98 * mm) + (
                simpleSplit(f"Note : {inv['notes']}", "Helvetica", 8, 98 * mm) if inv.get("notes") else []):
            pdf.drawString(M, ly, ln)
            ly -= 4 * mm
        # ---- colonne droite : totaux
        ty = top_y
        tx = W - M - 70 * mm
        lines = [("Total HT", fmt(ht))]
        if d:
            lines += [(f"Remise ({d:g}%)", "- " + fmt(rem)), ("HT après remise", fmt(net))]
        lines.append(("TVA", fmt(tva)))
        for lab, val in lines:
            pdf.setFillColor(gray)
            pdf.setFont("Helvetica", 9.5)
            pdf.drawString(tx, ty, lab)
            pdf.setFillColor(dark)
            pdf.drawRightString(W - M - 2 * mm, ty, val)
            ty -= 6.5 * mm
        pdf.setFillColor(red)
        pdf.rect(tx - 3 * mm, ty - 3 * mm, 73 * mm, 10 * mm, fill=1, stroke=0)
        pdf.setFillColor(colors.white)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(tx, ty + 0.5 * mm, "TOTAL TTC")
        pdf.drawRightString(W - M - 2 * mm, ty + 0.5 * mm, fmt(ttc))
        if inv["doctype"] == "FAC":
            paid = paid_of(inv)
            if paid > 0:
                ty -= 9 * mm
                for lab, val, col in (("Déjà réglé", fmt(paid), GREEN), ("Reste à payer", fmt(max(ttc - paid, 0)), RED)):
                    pdf.setFillColor(gray)
                    pdf.setFont("Helvetica", 9.5)
                    pdf.drawString(tx, ty, lab)
                    pdf.setFillColor(colors.HexColor(col))
                    pdf.setFont("Helvetica-Bold", 9.5)
                    pdf.drawRightString(W - M - 2 * mm, ty, val)
                    ty -= 6 * mm
        # ---- cachet / signature
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica-Bold", 8.5)
        pdf.drawString(M, 43.5 * mm, "Cachet de l'entreprise")
        pdf.drawString(W - M - 70 * mm, 43.5 * mm, cfg["sign_right"])
        pdf.setStrokeColor(gray)
        pdf.setLineWidth(0.6)
        pdf.roundRect(M, 24 * mm, 68 * mm, 17 * mm, 2 * mm, fill=0, stroke=1)
        pdf.roundRect(W - M - 70 * mm, 24 * mm, 70 * mm, 17 * mm, 2 * mm, fill=0, stroke=1)
        if inv["doctype"] == "FAC" and inv["status"] == "Payée":  # tampon PAYÉE
            pdf.saveState()
            pdf.setFillAlpha(0.13)
            pdf.setFillColor(colors.HexColor(GREEN))
            pdf.translate(W / 2, H / 2)
            pdf.rotate(30)
            pdf.setFont("Helvetica-Bold", 90)
            pdf.drawCentredString(0, 0, "PAYÉE")
            pdf.restoreState()
        footer()
        pdf.save()

    # ---------------------------------------------------- Stock produits
    def show_stock(self):
        b = self.page("stock", "Stock produits", "Double-clic : fiche complète  •  clic sur un en-tête : trier.")
        rows = load_products()
        low = [p for p in rows if p["stock"] <= p["min_stock"]]
        self.stat_cards(b, [("Produits", str(len(rows)), GRAY),
                            ("Unités en stock", f"{sum(p['stock'] for p in rows):g}", DARK),
                            ("Valeur (achat)", fmt(sum(p["stock"] * p["purchase_price"] for p in rows)), DARK),
                            ("Valeur (vente)", fmt(sum(p["stock"] * p["sale_price"] for p in rows)), GREEN),
                            ("Stock bas / rupture", str(len(low)), RED)])
        bar = ctk.CTkFrame(b, fg_color="transparent")
        bar.pack(fill="x", pady=4)
        search = ctk.CTkEntry(bar, placeholder_text="🔍 Référence, nom, marque, code-barres...", width=340, height=38)
        search.pack(side="left")
        cat = ctk.CTkComboBox(bar, values=["Toutes"] + sorted({p["category"] for p in rows if p["category"]}), width=190, height=38)
        cat.set("Toutes")
        cat.pack(side="left", padx=8)
        only_low = ctk.CTkCheckBox(bar, text="Alertes seulement")
        only_low.pack(side="left", padx=8)
        self.btn(bar, "＋  Nouveau produit", lambda: self.product_form(), width=180).pack(side="right")
        f = ctk.CTkFrame(b)
        f.pack(fill="both", expand=True, pady=6)
        cols = ("ref", "name", "category", "brand", "stock", "min", "buy", "sell", "loc", "state")
        t = ttk.Treeview(f, columns=cols, show="headings", height=10)
        for c, l, w, a in zip(cols, ["Réf", "Désignation", "Catégorie", "Marque", "Stock", "Min", "Prix achat", "Prix vente", "Emplacement", "État"],
                              [100, 260, 130, 110, 70, 60, 100, 100, 110, 90],
                              ["w", "w", "w", "w", "e", "e", "e", "e", "w", "w"]):
            t.heading(c, text=l)
            t.column(c, width=w, anchor=a)
        t.tag_configure("Rupture", foreground=RED)
        t.tag_configure("Stock bas", foreground=ORANGE)
        t.tag_configure("OK", foreground=DARK)
        sc = ttk.Scrollbar(f, command=t.yview)
        t.configure(yscrollcommand=sc.set)
        sc.pack(side="right", fill="y", pady=8)
        t.pack(fill="both", expand=True, padx=(8, 0), pady=8)
        make_sortable(t)

        def refresh(*_):
            q, c, ol = search.get().lower(), cat.get(), only_low.get()
            t.delete(*t.get_children())
            for p in rows:
                hay = " ".join(str(p[k]) for k in ("ref", "name", "brand", "category", "barcode", "supplier")).lower()
                if q in hay and (c == "Toutes" or p["category"] == c) and (not ol or product_status(p) != "OK"):
                    st = product_status(p)
                    t.insert("", "end", iid=str(p["id"]), tags=(st,),
                             values=(p["ref"], p["name"], p["category"], p["brand"], f"{p['stock']:g}", f"{p['min_stock']:g}",
                                     f"{p['purchase_price']:.2f}", f"{p['sale_price']:.2f}", p["location"], st))
        refresh()
        search.bind("<KeyRelease>", refresh)
        cat.configure(command=refresh)
        only_low.configure(command=refresh)

        def sel():
            return self.selected(t, "un produit")

        def edit(*_):
            i = sel()
            if i:
                self.product_form(i)

        def adjust():
            i = sel()
            if i:
                self.adjust_stock(i)

        def history():
            i = sel()
            if i:
                self.show_movements(i)

        def delete():
            i = sel()
            if i and messagebox.askyesno("Supprimer", "Supprimer ce produit et son historique de mouvements ?"):
                DB.products.delete_one({"id": i})
                DB.movements.delete_many({"product_id": i})
                self.show_stock()

        def export():
            p = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
            if p:
                keys = [k for _, k in PFIELDS]
                with open(p, "w", newline="", encoding="utf-8-sig") as fh:
                    w = csv.writer(fh, delimiter=";")
                    w.writerow([l.replace(" *", "") for l, _ in PFIELDS] + ["Créé le"])
                    for r in rows:
                        w.writerow([r[k] for k in keys] + [r["created"]])
                messagebox.showinfo("Export", "Export CSV terminé.")
        t.bind("<Double-1>", edit)
        act = ctk.CTkFrame(b, fg_color="transparent")
        act.pack(fill="x", pady=4)
        self.btn(act, "Fiche / Modifier", edit, "gray", width=140).pack(side="left", padx=3)
        self.btn(act, "± Ajuster le stock", adjust, "green", width=150).pack(side="left", padx=3)
        self.btn(act, "Historique", history, "gray", width=110).pack(side="left", padx=3)
        self.btn(act, "Exporter CSV", export, "gray", width=120).pack(side="left", padx=3)
        self.btn(act, "Supprimer", delete, width=110).pack(side="right", padx=3)

    def product_form(self, pid=None):
        p = get_product(pid) if pid else None
        top = self.dialog("Fiche produit" if pid else "Nouveau produit", "820x700")
        head = ctk.CTkFrame(top, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(14, 2))
        ctk.CTkLabel(head, text=(p["name"] if p else "Nouveau produit"), text_color=RED,
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        if p and p["image"] and os.path.isfile(p["image"]):
            try:
                im = Image.open(p["image"])
                im.thumbnail((80, 80))
                top._img = ctk.CTkImage(im, size=im.size)
                ctk.CTkLabel(head, image=top._img, text="").pack(side="right")
            except Exception:
                pass
        g = ctk.CTkFrame(top)
        g.pack(fill="both", expand=True, padx=16, pady=6)
        ents = {}
        defaults = {"unit": "pièce", "vat": "20", "stock": "0", "min_stock": "0", "purchase_price": "0", "sale_price": "0"}
        for i, (lab, key) in enumerate(PFIELDS):
            r, c = divmod(i, 2)
            c *= 2
            ctk.CTkLabel(g, text=lab).grid(row=r, column=c, padx=10, pady=6, sticky="w")
            e = ctk.CTkEntry(g, width=270)
            e.grid(row=r, column=c + 1, padx=10, pady=6, sticky="ew")
            val = p[key] if p else defaults.get(key, "")
            if key in PNUMS and p:
                val = f"{val:g}"
            e.insert(0, str(val if val is not None else ""))
            if key == "stock" and p:
                e.configure(state="disabled")
            ents[key] = e
        if p:
            ctk.CTkLabel(top, text=f"Marge unitaire : {p['sale_price'] - p['purchase_price']:.2f} DH   |   "
                                   f"Stock actuel : {p['stock']:g} {p['unit']}   |   Créé le {p['created']}",
                         text_color=GRAY).pack(anchor="w", padx=20)

        def pick_img():
            f = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")])
            if f:
                ents["image"].delete(0, "end")
                ents["image"].insert(0, f)

        def save():
            try:
                v = {k: e.get().strip() for k, e in ents.items()}
                for k in PNUMS:
                    v[k] = num(v[k])
            except ValueError:
                return messagebox.showerror("Données invalides", "Vérifiez les prix, la TVA et les quantités.", parent=top)
            if not v["ref"] or not v["name"]:
                return messagebox.showwarning("Champs obligatoires", "Référence et désignation sont obligatoires.", parent=top)
            try:
                if pid:
                    DB.products.update_one({"id": pid}, {"$set": {k: v[k] for _, k in PFIELDS if k != "stock"}})
                else:
                    doc = {k: v[k] for _, k in PFIELDS}
                    initial, doc["stock"] = v["stock"], 0
                    doc["id"], doc["created"] = next_id("products"), today()
                    DB.products.insert_one(doc)
                    if initial > 0:
                        move_stock(doc["id"], initial, "Stock initial", "Création du produit")
            except DuplicateKeyError:
                return messagebox.showerror("Référence existante", "Cette référence existe déjà.", parent=top)
            top.destroy()
            self.show_stock()
        row = ctk.CTkFrame(top, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=12)
        self.btn(row, "Enregistrer", save, width=140).pack(side="right", padx=4)
        self.btn(row, "Annuler", top.destroy, "gray", width=100).pack(side="right", padx=4)
        self.btn(row, "Choisir une image...", pick_img, "gray", width=160).pack(side="left")

    def adjust_stock(self, pid):
        p = get_product(pid)
        top = self.dialog("Ajuster le stock", "420x340")
        ctk.CTkLabel(top, text=p["name"], text_color=RED, font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(16, 0))
        ctk.CTkLabel(top, text=f"Stock actuel : {p['stock']:g} {p['unit']}", text_color=GRAY).pack(pady=(0, 8))
        kinds = ["Entrée (+)", "Sortie (−)", "Inventaire (définir la quantité)"]
        kind = ctk.CTkComboBox(top, values=kinds, width=300)
        kind.set(kinds[0])
        kind.pack(pady=6)
        qty = ctk.CTkEntry(top, placeholder_text="Quantité", width=300)
        qty.pack(pady=6)
        reason = ctk.CTkEntry(top, placeholder_text="Motif (réception, casse, inventaire...)", width=300)
        reason.pack(pady=6)

        def apply():
            try:
                q = num(qty.get())
                if q < 0:
                    raise ValueError
            except ValueError:
                return messagebox.showerror("Quantité invalide", "Saisissez une quantité positive.", parent=top)
            k, why = kind.get(), reason.get().strip()
            if k.startswith("Entrée"):
                move_stock(pid, q, "Entrée", why)
            elif k.startswith("Sortie"):
                move_stock(pid, -q, "Sortie", why)
            else:
                move_stock(pid, q - p["stock"], "Inventaire", why or "Inventaire")
            top.destroy()
            self.show_stock()
        self.btn(top, "Valider", apply, width=140).pack(pady=14)

    def show_movements(self, pid):
        p = get_product(pid)
        mv = list(DB.movements.find({"product_id": pid}, {"_id": 0}).sort("id", DESCENDING))
        top = self.dialog("Historique des mouvements", "760x480")
        ctk.CTkLabel(top, text=f"Mouvements : {p['name']}", text_color=RED,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(14, 6))
        cols = ("date", "kind", "qty", "reason", "doc")
        t = ttk.Treeview(top, columns=cols, show="headings")
        for c, l, w in zip(cols, ["Date", "Type", "Quantité", "Motif", "Document"], [140, 150, 90, 230, 130]):
            t.heading(c, text=l)
            t.column(c, width=w, anchor="e" if c == "qty" else "w")
        for m in mv:
            t.insert("", "end", values=(m["date"], m["kind"], f"{m['qty']:+g}", m.get("reason", ""), m.get("doc", "")))
        t.pack(fill="both", expand=True, padx=14, pady=10)

    # ---------------------------------------------------- Clients / Fournisseurs
    def show_clients(self):
        b = self.page("clients", "Clients & Fournisseurs", "Annuaire alimenté automatiquement à chaque document. Double-clic : modifier.")
        rows = list(DB.clients.find({}, {"_id": 0}).sort("name", ASCENDING))
        ca = {}
        for r in DB.invoices.find({"doctype": "FAC"}, {"client": 1, "total": 1, "_id": 0}):
            ca[r["client"]] = ca.get(r["client"], 0) + r["total"]
        bar = ctk.CTkFrame(b, fg_color="transparent")
        bar.pack(fill="x", pady=4)
        search = ctk.CTkEntry(bar, placeholder_text="🔍 Nom, ICE, téléphone...", width=320, height=38)
        search.pack(side="left")
        typ = ctk.CTkComboBox(bar, values=["Tous", "Client", "Fournisseur"], width=160, height=38)
        typ.set("Tous")
        typ.pack(side="left", padx=8)
        self.btn(bar, "＋  Nouveau tiers", lambda: self.client_form(), width=170).pack(side="right")
        f = ctk.CTkFrame(b)
        f.pack(fill="both", expand=True, pady=6)
        cols = ("name", "type", "phone", "email", "ice", "ca")
        t = ttk.Treeview(f, columns=cols, show="headings", height=14)
        for c, l, w in zip(cols, ["Nom", "Type", "Téléphone", "Email", "ICE", "CA facturé"], [280, 110, 130, 230, 150, 130]):
            t.heading(c, text=l)
            t.column(c, width=w, anchor="e" if c == "ca" else "w")
        t.pack(fill="both", expand=True, padx=8, pady=8)
        make_sortable(t)

        def refresh(*_):
            q, ty = search.get().lower(), typ.get()
            t.delete(*t.get_children())
            for c in rows:
                hay = " ".join(str(c.get(k, "")) for k in ("name", "ice", "phone", "email")).lower()
                if q in hay and (ty == "Tous" or c["type"] == ty):
                    t.insert("", "end", iid=str(c["id"]),
                             values=(c["name"], c["type"], c.get("phone", ""), c.get("email", ""), c.get("ice", ""),
                                     fmt(ca.get(c["name"], 0)) if c["type"] == "Client" else ""))
        refresh()
        search.bind("<KeyRelease>", refresh)
        typ.configure(command=refresh)

        def edit(*_):
            i = self.selected(t, "un tiers")
            if i:
                self.client_form(i)

        def delete():
            i = self.selected(t, "un tiers")
            if i and messagebox.askyesno("Supprimer", "Supprimer ce tiers de l'annuaire ?\n(Les documents existants sont conservés.)"):
                DB.clients.delete_one({"id": i})
                self.show_clients()
        t.bind("<Double-1>", edit)
        act = ctk.CTkFrame(b, fg_color="transparent")
        act.pack(fill="x", pady=4)
        self.btn(act, "Modifier", edit, "gray", width=120).pack(side="left", padx=3)
        self.btn(act, "Supprimer", delete, width=120).pack(side="right", padx=3)

    def client_form(self, cid=None):
        c = DB.clients.find_one({"id": cid}, {"_id": 0}) if cid else {}
        top = self.dialog("Tiers", "520x520")
        ctk.CTkLabel(top, text=c.get("name", "Nouveau tiers"), text_color=RED,
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(16, 8))
        g = ctk.CTkFrame(top)
        g.pack(fill="both", expand=True, padx=16)
        ents = {}
        for i, (lab, key) in enumerate([("Nom *", "name"), ("Type", "type"), ("ICE", "ice"), ("Adresse", "address"),
                                        ("Téléphone", "phone"), ("Email", "email"), ("Notes", "notes")]):
            ctk.CTkLabel(g, text=lab).grid(row=i, column=0, padx=10, pady=8, sticky="w")
            if key == "type":
                e = ctk.CTkComboBox(g, values=["Client", "Fournisseur"], width=300)
                e.set(c.get("type", "Client"))
            else:
                e = ctk.CTkEntry(g, width=300)
                e.insert(0, c.get(key, ""))
            e.grid(row=i, column=1, padx=10, pady=8)
            ents[key] = e

        def save():
            v = {k: e.get().strip() for k, e in ents.items()}
            if not v["name"]:
                return messagebox.showwarning("Nom requis", "Le nom est obligatoire.", parent=top)
            if cid:
                DB.clients.update_one({"id": cid}, {"$set": v})
            else:
                DB.clients.insert_one({**v, "id": next_id("clients"), "created": today()})
            top.destroy()
            self.show_clients()
        row = ctk.CTkFrame(top, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=12)
        self.btn(row, "Enregistrer", save, width=140).pack(side="right", padx=4)
        self.btn(row, "Annuler", top.destroy, "gray", width=100).pack(side="right", padx=4)

    # ---------------------------------------------------- Paramètres
    def show_settings(self):
        b = self.page("set", "Paramètres", "Coordonnées et mentions légales affichées sur vos documents.")
        g = ctk.CTkFrame(b)
        g.pack(fill="x", pady=4)
        ents = {}
        for i, (lab, key) in enumerate([("Entreprise", "company"), ("Adresse", "address"), ("Téléphone", "phone"),
                                        ("Email", "email"), ("ICE", "ice"), ("IF", "if"), ("RC", "rc"), ("TP", "tp"),
                                        ("RIB", "rib"), ("Conditions facture", "conditions"),
                                        ("Conditions devis", "conditions_devis"), ("Conditions bon de commande", "conditions_bc")]):
            r, c = divmod(i, 2)
            c *= 2
            ctk.CTkLabel(g, text=lab).grid(row=r, column=c, padx=10, pady=7, sticky="w")
            e = ctk.CTkEntry(g, width=330)
            e.grid(row=r, column=c + 1, padx=10, pady=7)
            e.insert(0, setting(key))
            ents[key] = e
        ctk.CTkLabel(b, text="Logo (PNG/JPG)").pack(anchor="w", pady=(12, 3))
        lr = ctk.CTkFrame(b, fg_color="transparent")
        lr.pack(anchor="w")
        logo = ctk.CTkEntry(lr, width=520)
        logo.pack(side="left")
        logo.insert(0, setting("logo"))

        def pick():
            p = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg")])
            if p:
                logo.delete(0, "end")
                logo.insert(0, p)
        self.btn(lr, "Parcourir...", pick, "gray", width=110).pack(side="left", padx=8)

        def save():
            for k, e in list(ents.items()) + [("logo", logo)]:
                DB.settings.update_one({"key": k}, {"$set": {"value": e.get().strip()}}, upsert=True)
            self.refresh_logo()
            messagebox.showinfo("Paramètres", "Informations enregistrées.")

        def backup():
            p = filedialog.asksaveasfilename(defaultextension=".json", initialfile=f"sauvegarde_{today()}.json",
                                             filetypes=[("JSON", "*.json")])
            if p:
                with open(p, "w", encoding="utf-8") as fh:
                    json.dump({n: list(DB[n].find({}, {"_id": 0})) for n in COLLS}, fh, ensure_ascii=False, indent=2, default=str)
                messagebox.showinfo("Sauvegarde", "Sauvegarde MongoDB terminée.")

        def restore():
            p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if not p or not messagebox.askyesno("Restaurer", "Cette action REMPLACE toutes les données actuelles.\nContinuer ?"):
                return
            try:
                with open(p, encoding="utf-8") as fh:
                    data = json.load(fh)
                for n in COLLS:
                    if n in data:
                        DB[n].delete_many({})
                        if data[n]:
                            DB[n].insert_many(data[n])
                DB.counters.delete_many({})
            except Exception as e:
                return messagebox.showerror("Erreur", str(e))
            messagebox.showinfo("Restauration", "Données restaurées.")
            self.refresh_logo()
            self.show_dashboard()
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.pack(anchor="w", pady=16)
        self.btn(row, "Enregistrer les paramètres", save, height=42, width=210).pack(side="left", padx=(0, 8))
        self.btn(row, "Sauvegarder la base", backup, "gray", height=42, width=180).pack(side="left", padx=(0, 8))
        self.btn(row, "Restaurer une sauvegarde", restore, "gray", height=42, width=200).pack(side="left")


if __name__ == "__main__":
    App().mainloop()