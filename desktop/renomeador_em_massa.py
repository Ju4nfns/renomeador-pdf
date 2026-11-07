#!/usr/bin/env python3
"""Renomeador em massa.

Só troca nomes (os.rename): o conteúdo dos arquivos nunca é lido, então tamanho e quantidade
não pesam na memória. Roda numa thread separada (a janela não trava), com progresso,
cancelamento seguro, log de erros e log para desfazer.
Uso: python renomeador_em_massa.py   (Python 3.8+ com tkinter)
"""
import csv, errno, os, queue, re, threading, time
from datetime import datetime
from itertools import islice

BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
LOGDIR = os.path.join(os.path.expanduser("~"), "Renomeador_logs")
BATCH = 200  # só controla a frequência de atualização da barra; não limita nada


def lp(p):
    """Windows: caminhos > 260 caracteres exigem o prefixo \\\\?\\ (a menos que o Windows esteja com caminhos longos ativos)."""
    p = os.path.abspath(p)
    return "\\\\?\\" + p if os.name == "nt" and len(p) > 240 and not p.startswith("\\\\") else p


def explain(e):
    if not isinstance(e, OSError):
        return f"nome inválido: {e}"
    if getattr(e, "winerror", None) == 206 or e.errno == errno.ENAMETOOLONG:
        return "caminho ou nome longo demais para o sistema (limite do Windows/disco)"
    if getattr(e, "winerror", None) == 32:
        return "arquivo em uso por outro programa"
    if isinstance(e, PermissionError):
        return "sem permissão (arquivo protegido ou somente leitura)"
    if isinstance(e, FileNotFoundError):
        return "arquivo não encontrado (foi movido ou apagado)"
    return str(e)


def make_name(stem, ext, n, p):
    if p["find"]:
        stem = re.sub(p["find"], p["repl"], stem) if p["regex"] else stem.replace(p["find"], p["repl"])
    out = BAD.sub("", p["tpl"].format(nome=stem, n=n)).strip().rstrip(". ")
    if not out or out.split(".")[0].upper() in RESERVED:
        raise ValueError("resultado vazio ou nome reservado do Windows")
    return out + ext


def iter_dirs(root, recursive, exts):
    """Lê uma pasta por vez (só nomes); nunca guarda a árvore inteira."""
    stack = [root]
    while stack:
        d = stack.pop()
        files = []
        try:
            with os.scandir(lp(d)) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        if recursive:
                            stack.append(os.path.join(d, e.name))
                    elif e.is_file(follow_symlinks=False) and (not exts or os.path.splitext(e.name)[1].lower() in exts):
                        files.append(e.name)
        except OSError as ex:
            yield d, None, None, ex
            continue
        files.sort(key=str.lower)
        for name in files:
            yield d, name, None, None


def plan(p):
    """Gera (pasta, nome_atual, nome_novo, erro) sob demanda, sem montar lista com todos os arquivos."""
    if p["csv"]:
        with open(p["csv"], newline="", encoding="utf-8-sig") as f:
            head = f.readline()
            f.seek(0)
            for row in csv.reader(f, delimiter=";" if head.count(";") >= head.count(",") else ","):
                if len(row) < 2 or not row[0].strip() or row[0].strip().lower() in ("atual", "original", "arquivo"):
                    continue
                old, new = row[0].strip(), BAD.sub("", row[1]).strip().rstrip(". ")
                d, name = os.path.split(old if os.path.isabs(old) else os.path.join(p["root"], old))
                if new and not os.path.splitext(new)[1]:
                    new += os.path.splitext(name)[1]
                yield d, name, new or None, (None if new else ValueError("novo nome vazio"))
        return
    cur, k = None, p["start"]
    for d, name, _, ex in iter_dirs(p["root"], p["recursive"], p["exts"]):
        if name is None:
            yield d, None, None, ex
            continue
        if d != cur:
            cur, k = d, p["start"]  # numeração {n} reinicia a cada pasta
        stem, ext = os.path.splitext(name)
        try:
            new, err = make_name(stem, ext, k, p), None
        except Exception as e:
            new, err = None, e
        k += 1
        yield d, name, new, err


def count(p):
    return sum(1 for x in plan(p) if x[1] is not None)


def rename_safe(d, src_name, wanted):
    """Renomeia sem nunca sobrescrever: se o nome já existe, vira 'nome (2)', 'nome (3)'..."""
    base, ext = os.path.splitext(wanted)
    src, k, new = os.path.join(d, src_name), 1, wanted
    while True:
        dst = os.path.join(d, new)
        if not os.path.lexists(lp(dst)) or os.path.samefile(lp(src), lp(dst)):
            break
        k += 1
        new = f"{base} ({k}){ext}"
    os.rename(lp(src), lp(dst))
    return new


def run(p, cancel, post):
    os.makedirs(LOGDIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    up, ep = os.path.join(LOGDIR, f"desfazer_{stamp}.csv"), os.path.join(LOGDIR, f"erros_{stamp}.csv")
    post(("status", "Contando arquivos…"))
    total, done, bad, t0, last = count(p), 0, 0, time.time(), 0.0
    with open(up, "w", newline="", encoding="utf-8-sig") as uf, open(ep, "w", newline="", encoding="utf-8-sig") as ef:
        uw, ew = csv.writer(uf, delimiter=";"), csv.writer(ef, delimiter=";")
        uw.writerow(["pasta", "nome_antigo", "nome_novo"])
        ew.writerow(["pasta", "arquivo", "erro"])
        for d, old, new, err in plan(p):
            if cancel.is_set():
                break
            if old is None:  # pasta que não pôde ser lida
                bad += 1
                ew.writerow([d, "", explain(err)])
                ef.flush()
                continue
            done += 1
            try:
                if err:
                    raise err
                if new != old:
                    new = rename_safe(d, old, new)
                    uw.writerow([d, old, new])
                    uf.flush()  # cada renomeação já fica registrada: dá para desfazer mesmo se o programa for fechado
            except Exception as e:
                bad += 1
                ew.writerow([d, old, explain(e)])
                ef.flush()
            if done % BATCH == 0 or time.time() - last > 0.3:
                last = time.time()
                post(("prog", done, total, bad, "Renomeando…"))
    post(("end", done, total, bad, "Cancelado com segurança" if cancel.is_set() else "Concluído", up, ep, time.time() - t0))


def run_undo(path, cancel, post):
    os.makedirs(LOGDIR, exist_ok=True)
    ep = os.path.join(LOGDIR, f"erros_desfazer_{datetime.now():%Y%m%d_%H%M%S}.csv")
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.reader(f, delimiter=";") if len(r) >= 3][1:]  # só nomes: leve
    total, done, bad, t0, last = len(rows), 0, 0, time.time(), 0.0
    with open(ep, "w", newline="", encoding="utf-8-sig") as ef:
        ew = csv.writer(ef, delimiter=";")
        ew.writerow(["pasta", "arquivo", "erro"])
        for d, old, new in reversed(rows):
            if cancel.is_set():
                break
            done += 1
            try:
                rename_safe(d, new, old)
            except Exception as e:
                bad += 1
                ew.writerow([d, new, explain(e)])
                ef.flush()
            if done % BATCH == 0 or time.time() - last > 0.3:
                last = time.time()
                post(("prog", done, total, bad, "Desfazendo…"))
    post(("end", done, total, bad, "Cancelado com segurança" if cancel.is_set() else "Desfeito", "", ep, time.time() - t0))


def main():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    win = tk.Tk()
    win.title("Renomeador em massa")
    win.minsize(780, 620)
    v = {k: tk.StringVar() for k in ("dir", "exts", "tpl", "find", "repl", "csv", "start")}
    v["exts"].set(".pdf"); v["tpl"].set("{nome}"); v["start"].set("1")
    rec, rx = tk.BooleanVar(value=True), tk.BooleanVar()
    q, cancel, busy = queue.Queue(), threading.Event(), [False]
    pad = dict(padx=6, pady=3)
    frm = ttk.Frame(win, padding=10)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)
    n = lambda x: f"{x:,}".replace(",", ".")

    def line(r, text, key, btn=None):
        ttk.Label(frm, text=text).grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=v[key]).grid(row=r, column=1, sticky="ew", **pad)
        if btn:
            ttk.Button(frm, text="Escolher…", command=btn).grid(row=r, column=2, **pad)

    line(0, "Pasta", "dir", lambda: v["dir"].set(filedialog.askdirectory() or v["dir"].get()))
    line(1, "Extensões (vazio = todas)", "exts")
    ttk.Checkbutton(frm, text="Incluir subpastas", variable=rec).grid(row=1, column=2, **pad)
    line(2, "Modelo do novo nome", "tpl")
    ttk.Label(frm, text="Use {nome} e {n} (ex.: NF_{n:04d} - {nome}). {n} reinicia em cada pasta; a extensão é mantida.",
              foreground="gray").grid(row=3, column=1, sticky="w", padx=6)
    line(4, "Localizar", "find")
    ttk.Checkbutton(frm, text="Expressão regular", variable=rx).grid(row=4, column=2, **pad)
    line(5, "Substituir por", "repl")
    line(6, "Numeração começa em", "start")
    line(7, "Ou tabela CSV (atual;novo)", "csv",
         lambda: v["csv"].set(filedialog.askopenfilename(filetypes=[("CSV", "*.csv")]) or v["csv"].get()))
    bar = ttk.Frame(frm)
    bar.grid(row=8, column=0, columnspan=3, sticky="w", pady=8)
    pb = ttk.Progressbar(frm, mode="determinate")
    pb.grid(row=9, column=0, columnspan=3, sticky="ew", **pad)
    lbl = ttk.Label(frm, text="Pronto.")
    lbl.grid(row=10, column=0, columnspan=3, sticky="w", **pad)
    box = tk.Text(frm, height=14, wrap="none", state="disabled")
    box.grid(row=11, column=0, columnspan=3, sticky="nsew", **pad)
    frm.rowconfigure(11, weight=1)

    def params():
        d = v["dir"].get().strip()
        if not os.path.isdir(d):
            raise ValueError("Escolha uma pasta válida.")
        tpl = v["tpl"].get() or "{nome}"
        try:
            tpl.format(nome="x", n=1)
            start = int(v["start"].get() or 1)
        except Exception:
            raise ValueError("Modelo ou número inicial inválido. Exemplo de modelo: NF_{n:04d} - {nome}")
        if rx.get() and v["find"].get():
            try:
                re.compile(v["find"].get())
            except re.error as e:
                raise ValueError(f"Expressão regular inválida: {e}")
        exts = {"." + x.strip().lstrip(".").lower() for x in v["exts"].get().replace(";", ",").split(",") if x.strip()}
        return dict(root=d, recursive=rec.get(), exts=exts, tpl=tpl, find=v["find"].get(), repl=v["repl"].get(),
                    regex=rx.get(), csv=v["csv"].get().strip(), start=start)

    def preview():
        try:
            p = params()
        except ValueError as e:
            return messagebox.showerror("Atenção", str(e))
        box.config(state="normal")
        box.delete("1.0", "end")
        for d, old, new, err in islice(plan(p), 200):
            box.insert("end", f"[pasta ilegível] {d}: {explain(err)}\n" if old is None
                       else f"{old}  →  {new if not err else '[erro: ' + str(err) + ']'}\n")
        box.insert("end", "\n(A pré-visualização mostra só os 200 primeiros para a tela responder rápido; a renomeação processa todos.)")
        box.config(state="disabled")

    def start(fn, *args):
        busy[0] = True
        cancel.clear()
        for b in (go_b, prev_b, un_b):
            b.config(state="disabled")
        cx_b.config(state="normal")

        def work():
            try:
                fn(*args, cancel, q.put)
            except Exception as e:
                q.put(("end", 0, 0, 0, f"Falha: {e}", "", "", 0))

        threading.Thread(target=work, daemon=True).start()

    def go():
        try:
            p = params()
        except ValueError as e:
            return messagebox.showerror("Atenção", str(e))
        if messagebox.askyesno("Confirmar", "Renomear os arquivos da pasta escolhida?\nUm registro para desfazer será salvo."):
            start(run, p)

    def undo():
        path = filedialog.askopenfilename(initialdir=LOGDIR, filetypes=[("Registro de desfazer", "desfazer_*.csv")])
        if path and messagebox.askyesno("Confirmar", "Desfazer essa operação?"):
            start(run_undo, path)

    def show(done, total, bad, st):
        pb["maximum"], pb["value"] = max(total, 1), done
        pct = f"{(100 * done / total) if total else 100:.1f}".replace(".", ",")
        lbl.config(text=f"{st}  {n(done)} de {n(total)} ({pct}%) — restam {n(max(total - done, 0))} — erros: {n(bad)}")

    def finish(m):
        _, done, total, bad, st, up, ep, secs = m
        busy[0] = False
        for b in (go_b, prev_b, un_b):
            b.config(state="normal")
        cx_b.config(state="disabled")
        show(done, total, bad, st)
        txt = f"{st}: {n(done)} de {n(total)} em {secs:.0f}s. Erros: {n(bad)}."
        if up:
            txt += f"\n\nPara desfazer, use o registro:\n{up}"
        if bad:
            txt += f"\n\nLista de erros:\n{ep}"
        messagebox.showinfo("Resultado", txt)

    def poll():
        try:
            while True:
                m = q.get_nowait()
                if m[0] == "status":
                    lbl.config(text=m[1])
                elif m[0] == "prog":
                    show(*m[1:])
                else:
                    finish(m)
        except queue.Empty:
            pass
        win.after(100, poll)

    prev_b = ttk.Button(bar, text="Pré-visualizar", command=preview)
    go_b = ttk.Button(bar, text="Renomear", command=go)
    cx_b = ttk.Button(bar, text="Cancelar", state="disabled",
                      command=lambda: (cancel.set(), lbl.config(text="Cancelando… terminando o arquivo atual")))
    un_b = ttk.Button(bar, text="Desfazer…", command=undo)
    for b in (prev_b, go_b, cx_b, un_b):
        b.pack(side="left", padx=4)
    poll()
    win.mainloop()


if __name__ == "__main__":
    main()
