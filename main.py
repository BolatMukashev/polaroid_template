#!/usr/bin/env python3
"""Polaroid Print: массовая раскладка фото на листе 152 x 101.5 мм (2 фото на лист).

Установка:  pip3 install pillow reportlab
Запуск:     python3 polaroid_print.py
(для HEIC: pip3 install pillow-heif)
"""
import os
import subprocess
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

# ---------- Шаблон (мм) ----------
PAGE_W, PAGE_H = 152.0, 101.5
PHOTO_W, PHOTO_H = 65.0, 75.0
SLOTS = [(5.5, 5.0), (82.0, 5.0)]  # x, y (от левого верхнего угла)
CUT_X = 76.0
DPI = 300
PX = 6  # пикселей на мм в предпросмотре


def load_image(path):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def crop_box(w, h, ox, oy):
    """Область исходника (в пикселях), попадающая в слот 65x75 мм (режим cover)."""
    s = max(PHOTO_W / w, PHOTO_H / h)  # мм на пиксель
    # min() защищает от погрешности float (cw может оказаться чуть больше w)
    cw, ch = min(PHOTO_W / s, w), min(PHOTO_H / s, h)
    left = min(max(ox * (w - cw), 0.0), w - cw)
    top = min(max(oy * (h - ch), 0.0), h - ch)
    return (left, top, left + cw, top + ch), s


class App:
    def __init__(self, root):
        self.root = root
        root.title("Polaroid Print 152×101,5")
        self.items = []  # {path, ox, oy, copies}
        self.cache = {}  # path -> уменьшенная копия для предпросмотра
        self.sheet = 0
        self.sel_slot = 0
        self.refs = []
        self.drag = None

        left = ttk.Frame(root, padding=6)
        left.pack(side="left", fill="y")
        ttk.Button(left, text="Добавить фото…", command=self.add).pack(fill="x")
        self.lb = tk.Listbox(left, width=34, height=26, exportselection=False)
        self.lb.pack(fill="y", expand=True, pady=4)
        self.lb.bind("<<ListboxSelect>>", self.on_select)
        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Button(row, text="↑", width=3, command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(row, text="↓", width=3, command=lambda: self.move(1)).pack(side="left")
        ttk.Button(row, text="Удалить", command=self.remove).pack(side="left", padx=4)
        ttk.Button(row, text="Очистить", command=self.clear).pack(side="left")
        row2 = ttk.Frame(left)
        row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="Копий листа:").pack(side="left")
        self.copies = tk.IntVar(value=1)
        ttk.Spinbox(row2, from_=1, to=99, width=4, textvariable=self.copies,
                    command=self.set_copies).pack(side="left", padx=4)
        ttk.Button(left, text="Центрировать фото", command=self.center).pack(fill="x")
        ttk.Button(left, text="Сохранить PDF…", command=self.save_pdf).pack(fill="x", pady=(10, 2))
        ttk.Button(left, text="Печать (открыть в Preview)", command=self.print_pdf).pack(fill="x")

        right = ttk.Frame(root, padding=6)
        right.pack(side="left")
        nav = ttk.Frame(right)
        nav.pack()
        ttk.Button(nav, text="◀", width=3, command=lambda: self.go(-1)).pack(side="left")
        self.lbl = ttk.Label(nav, text="Лист 0 / 0", width=18, anchor="center")
        self.lbl.pack(side="left")
        ttk.Button(nav, text="▶", width=3, command=lambda: self.go(1)).pack(side="left")
        self.cv = tk.Canvas(right, width=int(PAGE_W * PX), height=int(PAGE_H * PX),
                            bg="#888", highlightthickness=0)
        self.cv.pack(pady=6)
        ttk.Label(right, text="Тяните фото мышью (или стрелками) — сдвиг в пределах слота."
                  ).pack()
        self.cv.bind("<ButtonPress-1>", self.press)
        self.cv.bind("<B1-Motion>", self.motion)
        root.bind("<Left>", lambda e: self.nudge(-0.03, 0))
        root.bind("<Right>", lambda e: self.nudge(0.03, 0))
        root.bind("<Up>", lambda e: self.nudge(0, -0.03))
        root.bind("<Down>", lambda e: self.nudge(0, 0.03))
        self.redraw()

    # ---------- данные ----------
    def n_sheets(self):
        return (len(self.items) + 1) // 2

    def preview_img(self, path):
        if path not in self.cache:
            im = load_image(path)
            im.thumbnail((1600, 1600))
            self.cache[path] = (im, None)
        return self.cache[path][0]

    def add(self):
        files = filedialog.askopenfilenames(
            title="Выберите фото",
            filetypes=[("Изображения", "*.jpg *.jpeg *.png *.heic *.tif *.tiff *.webp *.bmp")])
        for f in sorted(files):
            self.items.append({"path": f, "ox": 0.5, "oy": 0.5, "copies": 1})
        self.refresh_list()
        self.redraw()

    def refresh_list(self):
        self.lb.delete(0, "end")
        for i, it in enumerate(self.items):
            self.lb.insert("end", f"{i // 2 + 1}.{i % 2 + 1}  {os.path.basename(it['path'])}")

    def cur_index(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def on_select(self, _):
        i = self.cur_index()
        if i is not None:
            self.sheet = i // 2
            self.sel_slot = i % 2
            self.copies.set(self.items[self.sheet * 2]["copies"])
            self.redraw()

    def move(self, d):
        i = self.cur_index()
        if i is None or not 0 <= i + d < len(self.items):
            return
        self.items[i], self.items[i + d] = self.items[i + d], self.items[i]
        self.refresh_list()
        self.lb.selection_set(i + d)
        self.on_select(None)

    def remove(self):
        i = self.cur_index()
        if i is None:
            return
        del self.items[i]
        self.refresh_list()
        self.sheet = min(self.sheet, max(self.n_sheets() - 1, 0))
        self.redraw()

    def clear(self):
        self.items.clear()
        self.cache.clear()
        self.sheet = 0
        self.refresh_list()
        self.redraw()

    def set_copies(self):
        # копии относятся к листу (хранятся у первого фото листа)
        for k in (self.sheet * 2, self.sheet * 2 + 1):
            if k < len(self.items):
                self.items[k]["copies"] = int(self.copies.get())

    def go(self, d):
        if 0 <= self.sheet + d < self.n_sheets():
            self.sheet += d
            k = self.sheet * 2
            self.copies.set(self.items[k]["copies"])
            self.redraw()

    def center(self):
        k = self.sheet * 2 + self.sel_slot
        if k < len(self.items):
            self.items[k]["ox"] = self.items[k]["oy"] = 0.5
            self.redraw()

    # ---------- предпросмотр ----------
    def redraw(self):
        self.cv.delete("all")
        self.refs.clear()
        self.lbl.config(text=f"Лист {self.sheet + 1 if self.items else 0} / {self.n_sheets()}")
        self.cv.create_rectangle(0, 0, PAGE_W * PX, PAGE_H * PX, fill="white", outline="")
        for s in range(2):
            k = self.sheet * 2 + s
            if k >= len(self.items):
                continue
            it = self.items[k]
            im = self.preview_img(it["path"])
            box, _ = crop_box(im.width, im.height, it["ox"], it["oy"])
            piece = im.crop(tuple(round(v) for v in box)).resize(
                (int(PHOTO_W * PX), int(PHOTO_H * PX)), Image.BILINEAR)
            tkimg = ImageTk.PhotoImage(piece)
            self.refs.append(tkimg)
            x, y = SLOTS[s]
            self.cv.create_image(x * PX, y * PX, image=tkimg, anchor="nw")
            if s == self.sel_slot:
                self.cv.create_rectangle(x * PX, y * PX, (x + PHOTO_W) * PX, (y + PHOTO_H) * PX,
                                         outline="#1a73e8", width=2)
        self.cv.create_line(CUT_X * PX, 0, CUT_X * PX, PAGE_H * PX, fill="#1a3a8f")

    def slot_at(self, x, y):
        for s, (sx, sy) in enumerate(SLOTS):
            if sx * PX <= x <= (sx + PHOTO_W) * PX and sy * PX <= y <= (sy + PHOTO_H) * PX:
                return s
        return None

    def press(self, e):
        s = self.slot_at(e.x, e.y)
        if s is None:
            return
        self.sel_slot = s
        self.drag = (e.x, e.y)
        k = self.sheet * 2 + s
        if k < len(self.items):
            self.lb.selection_clear(0, "end")
            self.lb.selection_set(k)
        self.redraw()

    def motion(self, e):
        if not self.drag:
            return
        dx, dy = (e.x - self.drag[0]) / PX, (e.y - self.drag[1]) / PX
        self.drag = (e.x, e.y)
        self.shift(dx, dy)

    def shift(self, dx_mm, dy_mm):
        k = self.sheet * 2 + self.sel_slot
        if k >= len(self.items):
            return
        it = self.items[k]
        im = self.preview_img(it["path"])
        s = max(PHOTO_W / im.width, PHOTO_H / im.height)
        ovx, ovy = im.width * s - PHOTO_W, im.height * s - PHOTO_H
        if ovx > 0.01:
            it["ox"] = min(1, max(0, it["ox"] - dx_mm / ovx))
        if ovy > 0.01:
            it["oy"] = min(1, max(0, it["oy"] - dy_mm / ovy))
        self.redraw()

    def nudge(self, fx, fy):
        k = self.sheet * 2 + self.sel_slot
        if k >= len(self.items):
            return
        it = self.items[k]
        it["ox"] = min(1, max(0, it["ox"] + fx))
        it["oy"] = min(1, max(0, it["oy"] + fy))
        self.redraw()

    # ---------- PDF ----------
    def build_pdf(self, path):
        c = pdfcanvas.Canvas(path, pagesize=(PAGE_W * mm, PAGE_H * mm))
        c.setTitle("Polaroid print")
        pw, ph = round(PHOTO_W / 25.4 * DPI), round(PHOTO_H / 25.4 * DPI)
        for sh in range(self.n_sheets()):
            imgs = []
            for s in range(2):
                k = sh * 2 + s
                if k < len(self.items):
                    it = self.items[k]
                    im = load_image(it["path"])
                    box, _ = crop_box(im.width, im.height, it["ox"], it["oy"])
                    imgs.append(im.resize((pw, ph), Image.LANCZOS, box=box))
            for _ in range(self.items[sh * 2]["copies"]):
                for s, piece in enumerate(imgs):
                    x, y = SLOTS[s]
                    c.drawImage(ImageReader(piece), x * mm, (PAGE_H - y - PHOTO_H) * mm,
                                PHOTO_W * mm, PHOTO_H * mm)
                c.setLineWidth(0.25)
                c.setStrokeColorRGB(0.1, 0.2, 0.55)
                c.line(CUT_X * mm, 0, CUT_X * mm, PAGE_H * mm)
                c.showPage()
        c.save()

    def save_pdf(self):
        if not self.items:
            return
        p = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if p:
            self.build_pdf(p)
            messagebox.showinfo("Готово", f"Сохранено: {p}")

    def print_pdf(self):
        if not self.items:
            return
        p = os.path.join(tempfile.gettempdir(), "polaroid_print.pdf")
        self.build_pdf(p)
        subprocess.run(["open", "-a", "Preview", p])


if __name__ == "__main__":
    r = tk.Tk()
    App(r)
    r.mainloop()