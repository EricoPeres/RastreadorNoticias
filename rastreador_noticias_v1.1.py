"""
Rastreador de Notícias (Google News RSS + Tkinter)

- Busca notícias de um assunto no Google News (sem chave de API).
- Você define o período (horas ou dias) ou desativa o limite.
- Lembra quais notícias já foram vistas (vistos.json) e destaca só as novas.
- Salva automaticamente os links das notícias novas em links_noticias.txt.
- Não precisa instalar nada: usa apenas a biblioteca padrão do Python.
"""

import csv
import json
import os
import platform
import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote
from urllib.request import Request, urlopen

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ARQ_HISTORICO = os.path.join(APP_DIR, "vistos.json")
ARQ_LINKS = os.path.join(APP_DIR, "links_noticias.txt")

# Idiomas disponíveis: código -> (rótulo, parâmetros do Google News)
IDIOMAS = {
    "pt": ("Português", "hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    "en": ("English", "hl=en-US&gl=US&ceid=US:en"),
}


# ----------------------------------------------------------------------
# Histórico (notícias já vistas)
# ----------------------------------------------------------------------
def carregar_historico():
    if not os.path.exists(ARQ_HISTORICO):
        return {}
    try:
        with open(ARQ_HISTORICO, encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, dict):
            return dados
    except (json.JSONDecodeError, OSError):
        pass
    # Arquivo corrompido: guarda uma cópia e começa do zero
    try:
        os.replace(ARQ_HISTORICO, ARQ_HISTORICO + ".bak")
    except OSError:
        pass
    return {}


def salvar_historico(historico):
    tmp = ARQ_HISTORICO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ARQ_HISTORICO)


def anexar_links(noticias, assunto):
    """Acrescenta as notícias novas ao arquivo de links (uma por linha)."""
    if not noticias:
        return
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(ARQ_LINKS, "a", encoding="utf-8") as f:
        for n in noticias:
            f.write(f"{agora}\t{assunto}\t{n['titulo']}\t{n['link']}\n")


# ----------------------------------------------------------------------
# Busca
# ----------------------------------------------------------------------
def buscar_noticias(assunto, valor, unidade, sem_limite, limite, idioma="pt"):
    consulta = assunto
    corte = None
    if not sem_limite:
        if unidade == "horas":
            consulta += f" when:{valor}h"
            corte = datetime.now(timezone.utc) - timedelta(hours=valor)
        else:
            consulta += f" when:{valor}d"
            corte = datetime.now(timezone.utc) - timedelta(days=valor)

    params_idioma = IDIOMAS.get(idioma, IDIOMAS["pt"])[1]
    url = f"https://news.google.com/rss/search?q={quote(consulta)}&{params_idioma}"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=20) as resp:
        raiz = ET.fromstring(resp.read())

    minimo = datetime.min.replace(tzinfo=timezone.utc)
    noticias = []
    for item in raiz.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        titulo = (item.findtext("title") or "").strip()
        fonte = (item.findtext("source") or "").strip()
        if fonte and titulo.endswith(" - " + fonte):
            titulo = titulo[: -(len(fonte) + 3)]

        data = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                data = parsedate_to_datetime(pub)
                if data.tzinfo is None:
                    data = data.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                data = None

        if corte and data and data < corte:
            continue
        noticias.append({"titulo": titulo, "fonte": fonte, "link": link, "data": data})

    noticias.sort(key=lambda n: n["data"] or minimo, reverse=True)
    return noticias[:limite]


def abrir_arquivo(caminho):
    sistema = platform.system()
    if sistema == "Windows":
        os.startfile(caminho)  # noqa: S606
    elif sistema == "Darwin":
        subprocess.run(["open", caminho], check=False)
    else:
        subprocess.run(["xdg-open", caminho], check=False)


# ----------------------------------------------------------------------
# Interface
# ----------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Rastreador de Notícias")
        self.geometry("1000x580")
        self.minsize(800, 420)

        self.historico = carregar_historico()
        self.exibidas = []
        self.por_item = {}
        self.fila = queue.Queue()

        self._montar_interface()
        self.after(150, self._verificar_fila)
        self._status(f"{len(self.historico)} notícias no histórico.")

    # ---------- layout ----------
    def _montar_interface(self):
        topo = ttk.Frame(self, padding=10)
        topo.pack(fill="x")

        ttk.Label(topo, text="Assunto:").grid(row=0, column=0, sticky="w")
        self.var_assunto = tk.StringVar()
        entrada = ttk.Entry(topo, textvariable=self.var_assunto, width=40)
        entrada.grid(row=0, column=1, columnspan=5, sticky="ew", padx=(6, 12))
        entrada.bind("<Return>", lambda e: self.buscar())
        entrada.focus()

        self.btn_buscar = ttk.Button(topo, text="Buscar", command=self.buscar)
        self.btn_buscar.grid(row=0, column=6, sticky="e")

        ttk.Label(topo, text="Idioma:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.var_idioma = tk.StringVar(value="pt")
        frame_idioma = ttk.Frame(topo)
        frame_idioma.grid(row=1, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(8, 0))
        for codigo, (rotulo, _) in IDIOMAS.items():
            ttk.Radiobutton(
                frame_idioma, text=rotulo, value=codigo, variable=self.var_idioma
            ).pack(side="left", padx=(0, 14))

        ttk.Label(topo, text="Período:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.var_valor = tk.IntVar(value=1)
        self.spin_valor = ttk.Spinbox(
            topo, from_=1, to=365, width=5, textvariable=self.var_valor
        )
        self.spin_valor.grid(row=2, column=1, sticky="w", padx=(6, 6), pady=(8, 0))

        self.var_unidade = tk.StringVar(value="dias")
        self.cmb_unidade = ttk.Combobox(
            topo, values=["horas", "dias"], textvariable=self.var_unidade,
            width=7, state="readonly",
        )
        self.cmb_unidade.grid(row=2, column=2, sticky="w", pady=(8, 0))

        self.var_sem_limite = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            topo, text="Sem limite de período", variable=self.var_sem_limite,
            command=self._alternar_periodo,
        ).grid(row=2, column=3, sticky="w", padx=12, pady=(8, 0))

        ttk.Label(topo, text="Máx. resultados:").grid(
            row=2, column=4, sticky="e", pady=(8, 0)
        )
        self.var_limite = tk.IntVar(value=50)
        ttk.Spinbox(topo, from_=5, to=100, width=5, textvariable=self.var_limite).grid(
            row=2, column=5, sticky="w", padx=6, pady=(8, 0)
        )

        self.var_so_novas = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            topo, text="Mostrar só novas", variable=self.var_so_novas
        ).grid(row=2, column=6, sticky="e", pady=(8, 0))

        topo.columnconfigure(1, weight=1)

        # Tabela
        meio = ttk.Frame(self, padding=(10, 0))
        meio.pack(fill="both", expand=True)

        colunas = ("status", "data", "titulo", "fonte")
        self.tree = ttk.Treeview(meio, columns=colunas, show="headings", selectmode="browse")
        self.tree.heading("status", text="Status")
        self.tree.heading("data", text="Publicada em")
        self.tree.heading("titulo", text="Título")
        self.tree.heading("fonte", text="Fonte")
        self.tree.column("status", width=60, anchor="center", stretch=False)
        self.tree.column("data", width=125, stretch=False)
        self.tree.column("titulo", width=560)
        self.tree.column("fonte", width=160, stretch=False)

        fonte_negrito = ("TkDefaultFont", 10, "bold")
        self.tree.tag_configure("nova", font=fonte_negrito, foreground="#0b5cad")
        self.tree.tag_configure("vista", foreground="#777777")

        barra = ttk.Scrollbar(meio, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=barra.set)
        self.tree.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self.abrir_selecionada())
        self.tree.bind("<Return>", lambda e: self.abrir_selecionada())
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Abrir no navegador", command=self.abrir_selecionada)
        self.menu.add_command(label="Copiar link", command=self.copiar_link)
        self.tree.bind("<Button-3>", self._menu_contexto)
        self.tree.bind("<Button-2>", self._menu_contexto)

        # Rodapé
        base = ttk.Frame(self, padding=10)
        base.pack(fill="x")
        ttk.Button(base, text="Exportar lista…", command=self.exportar).pack(side="left")
        ttk.Button(base, text="Abrir arquivo de links", command=self.abrir_links).pack(
            side="left", padx=6
        )
        ttk.Button(base, text="Limpar histórico", command=self.limpar_historico).pack(
            side="left"
        )
        self.var_status = tk.StringVar()
        ttk.Label(base, textvariable=self.var_status, anchor="e").pack(
            side="right", fill="x", expand=True
        )

    # ---------- utilidades ----------
    def _status(self, texto):
        self.var_status.set(texto)

    def _alternar_periodo(self):
        estado = "disabled" if self.var_sem_limite.get() else "normal"
        self.spin_valor.configure(state=estado)
        self.cmb_unidade.configure(
            state="disabled" if self.var_sem_limite.get() else "readonly"
        )

    def _menu_contexto(self, evento):
        item = self.tree.identify_row(evento.y)
        if item:
            self.tree.selection_set(item)
            self.menu.tk_popup(evento.x_root, evento.y_root)

    def _link_selecionado(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.por_item.get(sel[0], {}).get("link")

    # ---------- busca ----------
    def buscar(self):
        assunto = self.var_assunto.get().strip()
        if not assunto:
            messagebox.showwarning("Atenção", "Digite um assunto para buscar.")
            return
        try:
            valor = max(1, int(self.var_valor.get()))
            limite = max(1, int(self.var_limite.get()))
        except (tk.TclError, ValueError):
            messagebox.showwarning("Atenção", "Período e máximo devem ser números inteiros.")
            return

        unidade = self.var_unidade.get()
        sem_limite = self.var_sem_limite.get()
        idioma = self.var_idioma.get()

        self.btn_buscar.configure(state="disabled")
        self._status("Buscando…")

        def trabalho():
            try:
                resultado = buscar_noticias(assunto, valor, unidade, sem_limite, limite, idioma)
                self.fila.put(("ok", assunto, resultado))
            except Exception as erro:  # noqa: BLE001
                self.fila.put(("erro", assunto, erro))

        threading.Thread(target=trabalho, daemon=True).start()

    def _verificar_fila(self):
        try:
            while True:
                tipo, assunto, dado = self.fila.get_nowait()
                self.btn_buscar.configure(state="normal")
                if tipo == "ok":
                    self._processar(assunto, dado)
                else:
                    self._status("Falha na busca.")
                    messagebox.showerror(
                        "Erro", f"Não foi possível buscar as notícias:\n{dado}"
                    )
        except queue.Empty:
            pass
        self.after(150, self._verificar_fila)

    def _processar(self, assunto, noticias):
        agora = datetime.now().strftime("%Y-%m-%d %H:%M")
        novas = []
        for n in noticias:
            n["nova"] = n["link"] not in self.historico
            if n["nova"]:
                self.historico[n["link"]] = {
                    "titulo": n["titulo"],
                    "fonte": n["fonte"],
                    "assunto": assunto,
                    "visto_em": agora,
                }
                novas.append(n)

        try:
            salvar_historico(self.historico)
            anexar_links(novas, assunto)
        except OSError as erro:
            messagebox.showerror("Erro ao salvar", f"Não foi possível gravar os arquivos:\n{erro}")

        self.exibidas = [n for n in noticias if n["nova"]] if self.var_so_novas.get() else noticias
        self._preencher_tabela()
        self._status(
            f"{len(noticias)} encontradas, {len(novas)} novas. "
            f"Histórico: {len(self.historico)} notícias."
        )

    def _preencher_tabela(self):
        self.tree.delete(*self.tree.get_children())
        self.por_item.clear()
        for n in self.exibidas:
            data = n["data"].astimezone().strftime("%d/%m/%Y %H:%M") if n["data"] else "—"
            item = self.tree.insert(
                "", "end",
                values=("NOVA" if n["nova"] else "vista", data, n["titulo"], n["fonte"]),
                tags=("nova" if n["nova"] else "vista",),
            )
            self.por_item[item] = n

    # ---------- ações ----------
    def abrir_selecionada(self):
        link = self._link_selecionado()
        if link:
            webbrowser.open(link)

    def copiar_link(self):
        link = self._link_selecionado()
        if link:
            self.clipboard_clear()
            self.clipboard_append(link)
            self._status("Link copiado.")

    def exportar(self):
        if not self.exibidas:
            messagebox.showinfo("Exportar", "Não há notícias na lista para exportar.")
            return
        caminho = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Texto", "*.txt")],
            initialfile="noticias",
        )
        if not caminho:
            return
        try:
            if caminho.lower().endswith(".txt"):
                with open(caminho, "w", encoding="utf-8") as f:
                    for n in self.exibidas:
                        f.write(f"{n['titulo']}\n{n['link']}\n\n")
            else:
                with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(["Status", "Publicada em", "Título", "Fonte", "Link"])
                    for n in self.exibidas:
                        data = n["data"].astimezone().strftime("%d/%m/%Y %H:%M") if n["data"] else ""
                        w.writerow(
                            ["nova" if n["nova"] else "vista", data, n["titulo"], n["fonte"], n["link"]]
                        )
            self._status(f"Lista exportada para {caminho}")
        except OSError as erro:
            messagebox.showerror("Erro", f"Não foi possível exportar:\n{erro}")

    def abrir_links(self):
        if not os.path.exists(ARQ_LINKS):
            messagebox.showinfo("Links", "Ainda não há links salvos. Faça uma busca primeiro.")
            return
        abrir_arquivo(ARQ_LINKS)

    def limpar_historico(self):
        if not messagebox.askyesno(
            "Limpar histórico",
            "Todas as notícias voltarão a aparecer como NOVAS na próxima busca.\n"
            "(O arquivo links_noticias.txt não será apagado.)\n\nContinuar?",
        ):
            return
        self.historico = {}
        try:
            salvar_historico(self.historico)
        except OSError as erro:
            messagebox.showerror("Erro", f"Não foi possível salvar:\n{erro}")
            return
        self._status("Histórico limpo.")


if __name__ == "__main__":
    App().mainloop()
