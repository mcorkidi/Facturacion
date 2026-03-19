#!/usr/bin/env python3
"""Tkinter UI to parse a tab-delimited invoice export and send it to EBI API."""

from __future__ import annotations

import csv
import json
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any
from urllib import error, request

API_URL_DEFAULT = "https://integracion.ebi-pac.com/api/Enviar"
TOKEN_DEFAULT = "vvslrbtgvsux_ws_ebi"


@dataclass
class InvoiceItem:
    codigo: str
    descripcion: str
    cantidad: float
    unidad: str
    precio_unitario: float
    total: float


class InvoiceParser:
    """Parses the custom tab-delimited sample format into items and summary values."""

    @staticmethod
    def _to_float(raw: str) -> float:
        clean = raw.replace(",", "").replace("$", "").strip()
        if not clean:
            return 0.0
        return float(clean)

    @staticmethod
    def _normalize_space(value: str) -> str:
        return " ".join(value.split())

    @classmethod
    def parse_tab_file(cls, file_path: str | Path) -> dict[str, Any]:
        invoice_number = ""
        issue_date = ""
        customer_name = ""
        payment_terms = ""
        items: list[InvoiceItem] = []
        subtotal = 0.0
        grand_total = 0.0
        item_header_index: dict[str, int] | None = None

        with open(file_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f, delimiter="\t")
            for row in reader:
                normalized_row = [cls._normalize_space(c) for c in row]
                cells = [c for c in normalized_row if c]
                if not cells and item_header_index is None:
                    continue

                if not invoice_number and "Factura Comercial No." in cells:
                    idx = cells.index("Factura Comercial No.")
                    if idx + 3 < len(cells):
                        invoice_number = cells[idx + 3]

                if not issue_date and "Fecha Factura" in cells:
                    idx = cells.index("Fecha Factura")
                    if idx + 1 < len(cells):
                        issue_date = cells[idx + 1]

                if not customer_name and "Cliente" in cells:
                    idx = cells.index("Cliente")
                    if idx + 2 < len(cells):
                        customer_name = cells[idx + 2]

                if not payment_terms and "Condiciones de pago" in cells:
                    idx = cells.index("Condiciones de pago")
                    if idx + 3 < len(cells):
                        payment_terms = cells[idx + 3]

                if "Subtotal Neto:" in cells:
                    sub_idx = cells.index("Subtotal Neto:")
                    if sub_idx + 1 < len(cells):
                        subtotal = cls._to_float(cells[sub_idx + 1])

                if "Monto Total :" in cells:
                    total_idx = cells.index("Monto Total :")
                    if total_idx + 1 < len(cells):
                        grand_total = cls._to_float(cells[total_idx + 1])

                if item_header_index is None and {"Referencia", "Descripcion"}.issubset(set(cells)):
                    item_header_index = {
                        value: idx
                        for idx, value in enumerate(normalized_row)
                        if value in {"Referencia", "Descripcion", "Cantidad", "Unidad", "Precio Unitario", "Total"}
                    }
                    continue

                if item_header_index is not None:
                    ref_idx = item_header_index.get("Referencia")
                    desc_idx = item_header_index.get("Descripcion")
                    qty_idx = item_header_index.get("Cantidad")
                    unit_idx = item_header_index.get("Unidad")
                    price_idx = item_header_index.get("Precio Unitario")
                    total_idx = item_header_index.get("Total")

                    if ref_idx is None or desc_idx is None:
                        continue

                    max_idx = max(
                        idx
                        for idx in (ref_idx, desc_idx, qty_idx, unit_idx, price_idx, total_idx)
                        if idx is not None
                    )
                    if len(normalized_row) <= max_idx:
                        continue

                    code = normalized_row[ref_idx]
                    description = normalized_row[desc_idx]
                    if not code or not description:
                        continue
                    if code in {"Subtotal Neto:", "Monto Total :"}:
                        continue

                    try:
                        quantity = cls._to_float(normalized_row[qty_idx]) if qty_idx is not None else 0.0
                        unit = normalized_row[unit_idx] if unit_idx is not None else ""
                        price = cls._to_float(normalized_row[price_idx]) if price_idx is not None else 0.0
                        line_total = cls._to_float(normalized_row[total_idx]) if total_idx is not None else 0.0
                    except ValueError:
                        continue

                    items.append(
                        InvoiceItem(
                            codigo=code,
                            descripcion=description,
                            cantidad=quantity,
                            unidad=unit,
                            precio_unitario=price,
                            total=line_total,
                        )
                    )
                    continue

                if "Referencia" in cells and "Descripcion" in cells:
                    try:
                        ref_idx = cells.index("Referencia")
                        description = cells[ref_idx + 2]
                        code = cells[ref_idx + 1]

                        # pattern in sample: code, description, cantidad, '/', 0, unidad, precio, total
                        quantity = cls._to_float(cells[ref_idx + 3])
                        unit = cells[ref_idx + 6]
                        price = cls._to_float(cells[ref_idx + 7])
                        line_total = cls._to_float(cells[ref_idx + 8])

                        items.append(
                            InvoiceItem(
                                codigo=code,
                                descripcion=description,
                                cantidad=quantity,
                                unidad=unit,
                                precio_unitario=price,
                                total=line_total,
                            )
                        )
                    except (IndexError, ValueError):
                        continue

        if not grand_total:
            grand_total = subtotal

        return {
            "invoice_number": invoice_number,
            "issue_date": issue_date,
            "customer_name": customer_name,
            "payment_terms": payment_terms,
            "subtotal": subtotal,
            "grand_total": grand_total,
            "items": items,
        }


def build_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    fecha_emision = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    issue_date = parsed.get("issue_date") or datetime.now().strftime("%d-%b-%y").upper()

    items = [
        {
            "descripcion": i.descripcion,
            "codigo": i.codigo,
            "unidadMedida": i.unidad,
            "cantidad": f"{i.cantidad:.2f}",
            "precioUnitario": f"{i.precio_unitario:.2f}",
            "precioItem": f"{i.total:.2f}",
            "valorTotal": f"{i.total:.2f}",
            "tasaITBMS": "0.00",
            "valorITBMS": "0.00",
            "listaItemOTI": [],
            "vehiculo": {},
            "medicina": {},
            "pedidoComercialItem": {},
        }
        for i in parsed["items"]
    ]

    payload = {
        "documento": {
            "codigoSucursalEmisor": "001",
            "tipoSucursal": "1",
            "datosTransaccion": {
                "tipoEmision": "01",
                "fechaInicioContingencia": "",
                "motivoContingencia": "",
                "tipoDocumento": "01",
                "numeroDocumentoFiscal": parsed.get("invoice_number", ""),
                "puntoFacturacionFiscal": "001",
                "fechaEmision": fecha_emision,
                "fechaSalida": fecha_emision,
                "naturalezaOperacion": "01",
                "tipoOperacion": "1",
                "destinoOperacion": "2",
                "formatoCAFE": "1",
                "entregaCAFE": "1",
                "envioContenedor": "1",
                "procesoGeneracion": "1",
                "tipoVenta": "1",
                "informacionInteres": f"Source date: {issue_date}; Terms: {parsed.get('payment_terms', '')}",
                "cliente": {
                    "tipoClienteFE": "02",
                    "tipoContribuyente": "2",
                    "numeroRUC": "",
                    "digitoVerificadorRUC": "",
                    "razonSocial": parsed.get("customer_name", "Consumidor Final"),
                    "direccion": "",
                    "codigoUbicacion": "",
                    "provincia": "",
                    "distrito": "",
                    "corregimiento": "",
                    "tipoIdentificacion": "99",
                    "nroIdentificacionExtranjero": "",
                    "paisExtranjero": "",
                    "telefono1": "",
                    "telefono2": "",
                    "telefono3": "",
                    "correoElectronico1": "",
                    "correoElectronico2": "",
                    "correoElectronico3": "",
                    "pais": "PA",
                    "paisOtro": "",
                },
                "datosFacturaExportacion": {},
                "listaDocsFiscalReferenciados": [],
                "listaAutorizadosDescargaFEyEventos": [],
            },
            "listaItems": items,
            "totalesSubTotales": {
                "totalPrecioNeto": f"{parsed['subtotal']:.2f}",
                "totalITBMS": "0.00",
                "totalISC": "0.00",
                "totalMontoGravado": f"{parsed['subtotal']:.2f}",
                "totalDescuento": "0.00",
                "totalAcarreoCobrado": "0.00",
                "valorSeguroCobrado": "0.00",
                "totalFactura": f"{parsed['grand_total']:.2f}",
                "totalValorRecibido": f"{parsed['grand_total']:.2f}",
                "vuelto": "0.00",
                "tiempoPago": "1",
                "nroItems": str(len(items)),
                "totalTodosItems": f"{sum(i.total for i in parsed['items']):.2f}",
                "listaDescBonificacion": [],
                "listaFormaPago": [
                    {
                        "formaPagoFact": "01",
                        "descFormaPago": parsed.get("payment_terms", "CONTADO"),
                        "valorCuotaPagada": f"{parsed['grand_total']:.2f}",
                    }
                ],
                "retencion": {},
                "listaPagoPlazo": [],
                "listaTotalOTI": [],
            },
            "pedidoComercialGlobal": {},
            "infoLogistica": {},
            "infoEntrega": {},
            "usoPosterior": {"cufe": ""},
            "listaExtras": [],
            "serialDispositivo": "",
        }
    }
    return payload


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Factura -> EBI API Sender")
        self.geometry("1000x700")

        self.parsed_data: dict[str, Any] | None = None
        self.payload: dict[str, Any] | None = None

        self.url_var = tk.StringVar(value=API_URL_DEFAULT)
        self.token_var = tk.StringVar(value=TOKEN_DEFAULT)
        self.file_var = tk.StringVar(value="")

        self._build_ui()

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="API URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.url_var, width=80).grid(row=0, column=1, padx=5, sticky="we")

        ttk.Label(top, text="Bearer Token").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.token_var, width=80, show="*").grid(row=1, column=1, padx=5, sticky="we")

        ttk.Button(top, text="Load TSV", command=self.load_file).grid(row=2, column=0, pady=8, sticky="w")
        ttk.Entry(top, textvariable=self.file_var, width=80).grid(row=2, column=1, padx=5, sticky="we")

        btns = ttk.Frame(top)
        btns.grid(row=3, column=1, sticky="w", pady=8)
        ttk.Button(btns, text="Build Payload", command=self.make_payload).pack(side="left", padx=4)
        ttk.Button(btns, text="Send Request", command=self.send_request).pack(side="left", padx=4)

        top.columnconfigure(1, weight=1)

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=10)

        left_frame = ttk.Labelframe(paned, text="Parsed Invoice")
        right_frame = ttk.Labelframe(paned, text="Payload / API Response")
        paned.add(left_frame, weight=1)
        paned.add(right_frame, weight=1)

        self.parsed_text = tk.Text(left_frame, wrap="word")
        self.parsed_text.pack(fill="both", expand=True)

        self.output_text = tk.Text(right_frame, wrap="word")
        self.output_text.pack(fill="both", expand=True)

    def load_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select tab-delimited invoice file",
            filetypes=[("Tab delimited", "*.txt *.tsv *.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        self.file_var.set(path)
        try:
            self.parsed_data = InvoiceParser.parse_tab_file(path)
            self.payload = None
            self._write_json(self.parsed_text, self._parsed_to_dict(self.parsed_data))
            self._write(self.output_text, "File loaded. Click 'Build Payload' to generate JSON.")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Parsing error", str(exc))

    @staticmethod
    def _parsed_to_dict(parsed: dict[str, Any]) -> dict[str, Any]:
        out = dict(parsed)
        out["items"] = [item.__dict__ for item in parsed["items"]]
        return out

    def make_payload(self) -> None:
        if not self.parsed_data:
            messagebox.showwarning("No data", "Load a tab-delimited file first.")
            return

        self.payload = build_payload(self.parsed_data)
        self._write_json(self.output_text, self.payload)

    def send_request(self) -> None:
        if not self.payload:
            messagebox.showwarning("No payload", "Build payload first.")
            return

        token = self.token_var.get().strip()
        if not token:
            messagebox.showwarning("Token missing", "Please provide a Bearer token.")
            return

        body = json.dumps(self.payload).encode("utf-8")
        req = request.Request(
            self.url_var.get().strip(),
            data=body,
            method="POST",
            headers={
                "accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

        try:
            with request.urlopen(req, timeout=45) as resp:
                status = resp.status
                response_body = resp.read().decode("utf-8", errors="replace")
                self._write(
                    self.output_text,
                    f"HTTP {status}\n\nRequest payload:\n{json.dumps(self.payload, indent=2, ensure_ascii=False)}"
                    f"\n\nResponse:\n{response_body}",
                )
                messagebox.showinfo("Success", f"Request completed with HTTP {status}")
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            self._write(self.output_text, f"HTTPError {exc.code}\n{error_body}")
            messagebox.showerror("HTTP Error", f"{exc.code}: {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            self._write(self.output_text, f"Error: {exc}")
            messagebox.showerror("Request Error", str(exc))

    @staticmethod
    def _write(widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="normal")

    def _write_json(self, widget: tk.Text, data: Any) -> None:
        self._write(widget, json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    App().mainloop()
