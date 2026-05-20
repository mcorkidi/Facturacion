#!/usr/bin/env python3
"""Tkinter UI to parse a tab-delimited invoice export and send it to EBI API."""


from __future__ import annotations
# try:
#     import pyi_splash
#     pyi_splash.close()
# except ImportError:
#     pass
import csv
import base64
import hashlib
import json
import os
import webbrowser
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any
from urllib import error, request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import pycountry
import pytz

DEMO_ON = True
if not DEMO_ON:
    API_URL_DEFAULT = "https://integracion.ebi-pac.com/api/Enviar"
    AUTH_URL_DEFAULT = "https://integracion.ebi-pac.com/api/Autenticacion"
else:
    API_URL_DEFAULT = "https://integraciondemo.ebi-pac.com/api/Enviar"
    AUTH_URL_DEFAULT = "https://integraciondemo.ebi-pac.com/api/Autenticacion"
TOKEN_DEFAULT = "vvslrbtgvsux_ws_ebi"
CREDENTIALS_FILE = Path.home() / ".facturacion_credentials.json"
ACTIVITY_LOG_FILE = Path.home() / ".facturacion_activity_log.json"


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
        print(raw)
        clean = raw.replace(",", "").replace("$", "").strip()
        if not clean:
            return 0.0
        return round(float(clean), 2)

    @staticmethod
    def _normalize_space(value: str) -> str:
        return " ".join(value.split())

    @classmethod
    def parse_tab_file(cls, file_path: str | Path) -> dict[str, Any]:
        invoice_number = ""
        issue_date = ""
        customer_name = ""
        payment_terms = ""
        country = ""
        items: list[InvoiceItem] = []
        subtotal = 0.0
        grand_total = 0.0
        gastos_total = 0.0
        gastos = 0.0
        item_header_index: dict[str, int] | None = None

        with open(file_path, "r", encoding="latin-1", newline="") as f:
            reader = csv.reader(f, delimiter="\t")
            for row in reader:
                normalized_row = [cls._normalize_space(c) for c in row]
                cells = [c for c in normalized_row if c]
                # print(f"DEBUG: Processing row: {cells}")  # Debug print to trace row processing
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
                
                if not country and "Cliente" in cells:
                    idx = cells.index("Cliente")
                    if idx + 4 < len(cells):
                        country = cells[idx + 4]

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

                if "Cubicaje:" not in cells:
                    code = cells[cells.index("Total")+7] if len(cells) > 0 else ""
                    description = cells[cells.index("Total")+8] if len(cells) > 0 else ""  
                    quantity = cls._to_float(cells[cells.index("Total")+9]) if len(cells) > 0 else 0.0
                    unit = "Docena"
                    price = cls._to_float(cells[cells.index("Total")+13]) if len(cells) > 0 else 0.0
                    line_total = cls._to_float(cells[cells.index("Total")+14]) if len(cells) > 0 else 0.0
                    # print(f"DEBUG: Parsed item - Code: {code}, Description: {description}, Quantity: {quantity}, Unit: {unit}, Price: {price}, Total: {line_total}")  # Debug print to trace item parsing
                    if cells[cells.index("Total")+11] != "0":
                        quantity += (float(cells[cells.index("Total")+11])/12)
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

                if "Total de bultos:" in cells:
                            # print(f"DEBUG: Found 'Total de bultos:' in row, processing gastos - Cells: {cells}")  # print to trace gastos processing
                            sub_idx = cells.index("Total de bultos:")
                            if sub_idx + 3 < len(cells):
                                gastos = cls._to_float(cells[sub_idx - 2])
                                gastos_total += gastos
                                items.append(
                                    InvoiceItem(
                                        codigo=cells[sub_idx - 3],
                                        descripcion=cells[sub_idx - 3],
                                        cantidad=1.0,
                                        unidad="und",
                                        precio_unitario=gastos,
                                        total=gastos,
                                    )
                                )
        
       

        if not grand_total:
            grand_total = subtotal + gastos_total

        return {
            "invoice_number": invoice_number,
            "issue_date": issue_date,
            "customer_name": customer_name,
            "country": country,
            "payment_terms": payment_terms,
            "subtotal": subtotal,
            "gastos": gastos_total,
            "grand_total": grand_total,
            "items": items,
        }


def build_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    # try:
    #     panama_tz = ZoneInfo("America/Panama")
    # except ZoneInfoNotFoundError:
    #     panama_tz = timezone(timedelta(hours=-5))
    # fecha_emision = datetime.now(panama_tz).isoformat(timespec="seconds")
    # issue_date = parsed.get("issue_date") or datetime.now().strftime("%d-%b-%y").upper()
    # Input date
    date_str = parsed["issue_date"]

    # Step 1: Parse date (day-month-year with abbreviated month)
    dt = datetime.strptime(date_str, "%d-%b-%y")

    # Get current time
    now = datetime.now()

    # Replace with current time
    dt = dt.replace(hour=now.hour, minute=now.minute, second=now.second)

    # Step 3: Add timezone (example: -05:00)
    timezone = pytz.timezone("Etc/GMT+5")  # GMT+5 corresponds to -05:00 offset
    dt = timezone.localize(dt)

    # Step 4: Convert to ISO 8601 string
    fecha_emision = dt.isoformat()

    items = [
        {
            "descripcion": i.descripcion,
            "codigo": i.codigo,
            "unidadMedida": i.unidad,
            "cantidad": f"{i.cantidad:.3f}",
            "precioUnitario": f"{i.precio_unitario:.2f}",
            "precioUnitarioDescuento": "", 
            "precioItem": f"{i.total:.2f}",
            "valorTotal": f"{i.total:.2f}",
            "tasaITBMS": "00",
            "valorITBMS": "00.00",
            
        }
        for i in parsed["items"]

    ]
    # print(f"DEBUG: Built items for payload: {items}")  # Debug print to trace item building
    try:
        country_code = pycountry.countries.search_fuzzy(parsed.get("country", "PA"))[0].alpha_2
    except (LookupError, IndexError):
        if parsed.get("country") == "DOMINICANA" or parsed.get("country") == "dominicana":
            country_code = "DO"
        else:
            country_code = "CR"
    payload = {
        "documento": {
            "codigoSucursalEmisor": "0000",
            "tipoSucursal": "1",
            "datosTransaccion": {
                "tipoEmision": "01",
                "fechaInicioContingencia": "",
                "motivoContingencia": "",
                "tipoDocumento": "08",
                "numeroDocumentoFiscal": parsed.get("invoice_number", ""),
                "puntoFacturacionFiscal": "001",
                "fechaEmision": fecha_emision,
                "fechaSalida": fecha_emision,
                "naturalezaOperacion": "01",
                "tipoOperacion": "1",
                "destinoOperacion": "1" if country_code == "PA" else "2",
                "formatoCAFE": "3",
                "entregaCAFE": "3",
                "envioContenedor": "1",
                "procesoGeneracion": "1",
                "tipoVenta": "",
                "informacionInteres": "Factura Zona Franca",
                "cliente": {
                    "tipoClienteFE": "04",
                    "tipoContribuyente": "",
                    "numeroRUC": "",
                    "razonSocial": parsed.get('customer_name', 'Cliente Extranjero'),
                    "direccion": "",
                    "codigoUbicacion": "",
                    "provincia": "",
                    "distrito": "",
                    "corregimiento": "",
                    "tipoIdentificacion": "01",
                    "nroIdentificacionExtranjero": "0000000",
                    "paisExtranjero": parsed.get("country", "PA"),
                    "telefono1": "",
                    "telefono2": "",
                    "telefono3": "",
                    "correoElectronico1": "",
                    "correoElectronico2": "",
                    "correoElectronico3": "",
                    "pais": country_code,
                    "paisOtro": "",
                },
                **({
                        "datosFacturaExportacion": {
                            "condicionesEntrega": "FOB",
                            "monedaOperExportacion": "USD",
                            "monedaOperExportacionNonDef": "",
                            "tipoDeCambio": "",
                            "montoMonedaExtranjera": "",
                            "puertoEmbarque": "Zona Libre de Colon"
                        }
                    } if not country_code == "PA" else {})
            },
            "listaItems": items,
            "totalesSubTotales": {
                "totalPrecioNeto": f"{parsed['grand_total']:.2f}",
                "totalITBMS": "0.00",
                "totalMontoGravado": "0.00",
                "totalFactura": f"{parsed['grand_total']:.2f}",
                "totalValorRecibido": f"{parsed['grand_total']:.2f}",
                "tiempoPago": "1",
                "nroItems": str(len(items)),
                "totalTodosItems": f"{sum(i.total for i in parsed['items']):.2f}",
                "totalOtrosGastos": parsed['gastos'] if 'gastos' in parsed else "0.00",
                "listaFormaPago": [
                    {
                        "formaPagoFact": "08",
                        "valorCuotaPagada": f"{parsed['grand_total']:.2f}",
                    }
                ],
            },
        }
    }
    return payload


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Factura -> EBI API Sender")
        self.geometry("1000x700")
        self.iconbitmap("icon.ico")
        self.parsed_data: dict[str, Any] | None = None
        self.payload: dict[str, Any] | None = None
        self.activity_log: dict[str, list[dict[str, Any]]] = {}
        self.log_tree: ttk.Treeview | None = None
        self.log_detail_text: tk.Text | None = None

        self.url_var = tk.StringVar(value=API_URL_DEFAULT)
        self.auth_url_var = tk.StringVar(value=AUTH_URL_DEFAULT)
        # self.auth_bearer_var = tk.StringVar(value=TOKEN_DEFAULT)
        self.username_var = tk.StringVar(value="")
        self.password_var = tk.StringVar(value="")
        self.auth_bearer_var = tk.StringVar(value=TOKEN_DEFAULT)
        self.file_var = tk.StringVar(value="")
        self.auto_open_qr_var = tk.BooleanVar(value=True)

        self._build_ui()
        self._load_saved_credentials()
        self._load_activity_log()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    @staticmethod
    def _machine_secret() -> bytes:
        system_hint = f"{os.name}|{os.getenv('USER', '')}|{os.getenv('USERNAME', '')}"
        return hashlib.sha256(system_hint.encode("utf-8")).digest()

    @classmethod
    def _encrypt_text(cls, plain_text: str) -> dict[str, str]:
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac("sha256", cls._machine_secret(), salt, 150_000, dklen=32)
        plain_bytes = plain_text.encode("utf-8")
        encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(plain_bytes))
        return {
            "salt": base64.b64encode(salt).decode("utf-8"),
            "value": base64.b64encode(encrypted).decode("utf-8"),
        }

    @classmethod
    def _decrypt_text(cls, payload: dict[str, str]) -> str:
        salt = base64.b64decode(payload["salt"].encode("utf-8"))
        encrypted = base64.b64decode(payload["value"].encode("utf-8"))
        key = hashlib.pbkdf2_hmac("sha256", cls._machine_secret(), salt, 150_000, dklen=32)
        plain_bytes = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))
        return plain_bytes.decode("utf-8")

    def _save_credentials(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get().strip()
        if not username or not password:
            return
        payload = {
            "username": self._encrypt_text(username),
            "password": self._encrypt_text(password),
        }
        CREDENTIALS_FILE.write_text(json.dumps(payload), encoding="utf-8")

    def _load_saved_credentials(self) -> None:
        if not CREDENTIALS_FILE.exists():
            return
        try:
            payload = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            username = self._decrypt_text(payload["username"])
            password = self._decrypt_text(payload["password"])
            self.username_var.set(username)
            self.password_var.set(password)
        except Exception:  # noqa: BLE001
            # If decryption fails (different machine/user/corrupt file), ignore and let user re-enter.
            return

    def _on_close(self) -> None:
        self._save_credentials()
        self.destroy()

    def _build_ui(self) -> None:
        menubar = tk.Menu(self)
        activity_menu = tk.Menu(menubar, tearoff=0)
        activity_menu.add_command(label="View Activity Log", command=self.open_activity_log_window)
        menubar.add_cascade(label="Activity", menu=activity_menu)
        self.config(menu=menubar)

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="API URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.url_var, width=80).grid(row=0, column=1, padx=5, sticky="we")

        ttk.Label(top, text="Auth URL").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.auth_url_var, width=80).grid(row=1, column=1, padx=5, sticky="we")

        # ttk.Label(top, text="Auth Bearer").grid(row=2, column=0, sticky="w")
        # ttk.Entry(top, textvariable=self.auth_bearer_var, width=80, show="*").grid(row=2, column=1, padx=5, sticky="we")

        creds = ttk.Frame(top)
        creds.grid(row=3, column=1, sticky="we", pady=5)
        creds.columnconfigure(1, weight=1)
        creds.columnconfigure(3, weight=1)

        ttk.Label(creds, text="Usuario").grid(row=0, column=0, sticky="w")
        ttk.Entry(creds, textvariable=self.username_var, width=30).grid(row=0, column=1, padx=(5, 12), sticky="we")
        ttk.Label(creds, text="Clave").grid(row=0, column=2, sticky="w")
        ttk.Entry(creds, textvariable=self.password_var, width=30, show="*").grid(row=0, column=3, padx=5, sticky="we")
        ttk.Button(creds, text="Obtener Token", command=self.get_auth_token).grid(row=0, column=4, padx=5, sticky="e")

        ttk.Label(top, text="Bearer Token").grid(row=4, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.auth_bearer_var, width=80, show="*").grid(row=4, column=1, padx=5, sticky="we")

        ttk.Button(top, text="Load TSV", command=self.load_file).grid(row=5, column=0, pady=8, sticky="w")
        ttk.Entry(top, textvariable=self.file_var, width=80).grid(row=5, column=1, padx=5, sticky="we")

        btns = ttk.Frame(top)
        btns.grid(row=6, column=1, sticky="w", pady=8)
        ttk.Button(btns, text="Build Payload", command=self.make_payload).pack(side="left", padx=4)
        ttk.Button(btns, text="Send Request", command=self.send_request).pack(side="left", padx=4)
        ttk.Checkbutton(
            btns,
            text="Auto-open QR link",
            variable=self.auto_open_qr_var,
        ).pack(side="left", padx=8)

        top.columnconfigure(1, weight=1)

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=10)

        left_frame = ttk.Labelframe(paned, text="Parsed Invoice")
        right_frame = ttk.Labelframe(paned, text="Payload / API Response")
        paned.add(left_frame, weight=1)
        paned.add(right_frame, weight=1)

        # Left text + scrollbar
        left_scroll = tk.Scrollbar(left_frame)
        left_scroll.pack(side="right", fill="y")

        self.parsed_text = tk.Text(left_frame, wrap="word", yscrollcommand=left_scroll.set)
        self.parsed_text.pack(side="left", fill="both", expand=True)

        left_scroll.config(command=self.parsed_text.yview)


        # Right text + scrollbar
        right_scroll = tk.Scrollbar(right_frame)
        right_scroll.pack(side="right", fill="y")

        self.output_text = tk.Text(right_frame, wrap="word", yscrollcommand=right_scroll.set)
        self.output_text.pack(side="left", fill="both", expand=True)

        right_scroll.config(command=self.output_text.yview)

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

    def get_auth_token(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get().strip()
        auth_bearer = self.auth_bearer_var.get().strip()
        auth_url = self.auth_url_var.get().strip()

        if not username or not password:
            messagebox.showwarning("Credenciales incompletas", "Ingresa usuario y clave para obtener el token.")
            return
        if not auth_bearer:
            messagebox.showwarning("Bearer faltante", "Ingresa el bearer de autenticación.")
            return
        if not auth_url:
            messagebox.showwarning("URL faltante", "Ingresa la URL de autenticación.")
            return

        auth_payload = {"usuario": username, "clave": password}
        req = request.Request(
            auth_url,
            data=json.dumps(auth_payload).encode("utf-8"),
            method="POST",
            headers={
                "accept": "*/*",
                "Authorization": f"Bearer {auth_bearer}",
                "Content-Type": "application/json",
            },
        )

        try:
            with request.urlopen(req, timeout=45) as resp:
                response_body = resp.read().decode("utf-8", errors="replace")
                data = json.loads(response_body)
                token = (data.get("token") or "").strip()
                if not token:
                    raise ValueError("La respuesta no contiene un token válido.")

                self.auth_bearer_var.set(token)
                expiracion = data.get("expiracion", "N/D")
                self._write(
                    self.output_text,
                    "Token obtenido y guardado para el envío de la factura.\n\n"
                    f"Expiración: {expiracion}\n\n"
                    f"Respuesta completa:\n{json.dumps(data, indent=2, ensure_ascii=False)}",
                )
                self._save_credentials()
                messagebox.showinfo("Token generado", "Token obtenido correctamente y cargado en Bearer Token.")
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            self._write(self.output_text, f"HTTPError {exc.code}\n{error_body}")
            messagebox.showerror("Error de autenticación", f"{exc.code}: {exc.reason}")
        except json.JSONDecodeError:
            self._write(self.output_text, "La respuesta de autenticación no es JSON válido.")
            messagebox.showerror("Error de autenticación", "La respuesta no tiene formato JSON válido.")
        except Exception as exc:  # noqa: BLE001
            self._write(self.output_text, f"Error: {exc}")
            messagebox.showerror("Error de autenticación", str(exc))

    def send_request(self) -> None:
        if not self.payload:
            messagebox.showwarning("No payload", "Build payload first.")
            return

        token = self.auth_bearer_var.get().strip()
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
                qr_url = self._extract_qr_url(response_body)
                invoice_number = (
                    self.payload.get("documento", {})
                    .get("datosTransaccion", {})
                    .get("numeroDocumentoFiscal", "")
                )
                self._append_activity_entry(
                    invoice_number=invoice_number,
                    status=f"HTTP {status}",
                    endpoint=self.url_var.get().strip(),
                    request_payload=self.payload,
                    response_body=response_body,
                )
                self._write(
                    self.output_text,
                    f"\n\nResponse:\n{response_body}"
                    f"HTTP {status}\n\nRequest payload:\n{json.dumps(self.payload, indent=2, ensure_ascii=False)}"
                    ,
                )
                messagebox.showinfo("Success", f"Request completed with HTTP {status}")
                if qr_url:
                    if self.auto_open_qr_var.get():
                        webbrowser.open(qr_url)
                        messagebox.showinfo("QR abierto", f"Se abrió el enlace QR en tu navegador:\n{qr_url}")
                    else:
                        messagebox.showinfo("QR disponible", f"Enlace QR recibido:\n{qr_url}")
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            invoice_number = ""
            if self.payload:
                invoice_number = (
                    self.payload.get("documento", {})
                    .get("datosTransaccion", {})
                    .get("numeroDocumentoFiscal", "")
                )
            self._append_activity_entry(
                invoice_number=invoice_number,
                status=f"HTTPError {exc.code}",
                endpoint=self.url_var.get().strip(),
                request_payload=self.payload,
                response_body=error_body,
            )
            self._write(self.output_text, f"HTTPError {exc.code}\n{error_body}")
            messagebox.showerror("HTTP Error", f"{exc.code}: {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            invoice_number = ""
            if self.payload:
                invoice_number = (
                    self.payload.get("documento", {})
                    .get("datosTransaccion", {})
                    .get("numeroDocumentoFiscal", "")
                )
            self._append_activity_entry(
                invoice_number=invoice_number,
                status="Request Error",
                endpoint=self.url_var.get().strip(),
                request_payload=self.payload,
                response_body=str(exc),
            )
            self._write(self.output_text, f"Error: {exc}")
            messagebox.showerror("Request Error", str(exc))

    def _append_activity_entry(
        self,
        invoice_number: str,
        status: str,
        endpoint: str,
        request_payload: dict[str, Any] | None,
        response_body: str,
    ) -> None:
        key = invoice_number.strip() or "UNKNOWN"
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "endpoint": endpoint,
            "request_payload": request_payload or {},
            "response_body": response_body,
            "qr_url": self._extract_qr_url(response_body) or "",
        }
        self.activity_log.setdefault(key, []).append(entry)
        self._save_activity_log()
        self._refresh_activity_tree()

    def _load_activity_log(self) -> None:
        if not ACTIVITY_LOG_FILE.exists():
            self.activity_log = {}
            return
        try:
            loaded = json.loads(ACTIVITY_LOG_FILE.read_text(encoding="utf-8"))
            self.activity_log = loaded if isinstance(loaded, dict) else {}
        except Exception:  # noqa: BLE001
            self.activity_log = {}

    def _save_activity_log(self) -> None:
        ACTIVITY_LOG_FILE.write_text(
            json.dumps(self.activity_log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def open_activity_log_window(self) -> None:
        if self.log_tree is not None and self.log_tree.winfo_exists():
            self.log_tree.winfo_toplevel().lift()
            return

        win = tk.Toplevel(self)
        win.title("Activity Log")
        win.geometry("1000x600")

        paned = ttk.Panedwindow(win, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.Labelframe(paned, text="Requests by Invoice Number")
        right = ttk.Labelframe(paned, text="Selected Response Detail")
        paned.add(left, weight=1)
        paned.add(right, weight=2)

        columns = ("invoice", "timestamp", "status", "qr")
        self.log_tree = ttk.Treeview(left, columns=columns, show="headings")
        self.log_tree.heading("invoice", text="Invoice")
        self.log_tree.heading("timestamp", text="Timestamp")
        self.log_tree.heading("status", text="Status")
        self.log_tree.heading("qr", text="QR Link")
        self.log_tree.column("invoice", width=180, anchor="w")
        self.log_tree.column("timestamp", width=180, anchor="w")
        self.log_tree.column("status", width=140, anchor="w")
        self.log_tree.column("qr", width=360, anchor="w")
        self.log_tree.pack(fill="both", expand=True)
        self.log_tree.bind("<<TreeviewSelect>>", self._on_log_select)
        self.log_tree.bind("<Double-1>", self._on_log_double_click)

        self.log_detail_text = tk.Text(right, wrap="word")
        self.log_detail_text.pack(fill="both", expand=True)

        def _on_close() -> None:
            self.log_tree = None
            self.log_detail_text = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)
        self._refresh_activity_tree()

    def _refresh_activity_tree(self) -> None:
        if self.log_tree is None:
            return
        try:
            if not self.log_tree.winfo_exists():
                self.log_tree = None
                self.log_detail_text = None
                return
            for row_id in self.log_tree.get_children():
                self.log_tree.delete(row_id)
        except tk.TclError:
            self.log_tree = None
            self.log_detail_text = None
            return

        for invoice_number, entries in sorted(self.activity_log.items()):
            for index, entry in enumerate(entries):
                self.log_tree.insert(
                    "",
                    "end",
                    iid=f"{invoice_number}|{index}",
                    values=(
                        invoice_number,
                        entry.get("timestamp", ""),
                        entry.get("status", ""),
                        entry.get("qr_url", ""),
                    ),
                )

    def _on_log_select(self, _: tk.Event) -> None:
        if not self.log_tree or not self.log_detail_text:
            return
        selected = self.log_tree.selection()
        if not selected:
            return
        row_id = selected[0]
        if "|" not in row_id:
            return
        invoice_number, index_raw = row_id.split("|", maxsplit=1)
        try:
            index = int(index_raw)
            entry = self.activity_log[invoice_number][index]
        except (ValueError, KeyError, IndexError):
            return

        self._write(
            self.log_detail_text,
            json.dumps(
                {
                    "invoice_number": invoice_number,
                    **entry,
                },
                indent=2,
                ensure_ascii=False,
            ),
        )

    def _on_log_double_click(self, event: tk.Event) -> None:
        if not self.log_tree:
            return
        row_id = self.log_tree.identify_row(event.y)
        column_id = self.log_tree.identify_column(event.x)
        if not row_id or column_id != "#4" or "|" not in row_id:
            return

        invoice_number, index_raw = row_id.split("|", maxsplit=1)
        try:
            index = int(index_raw)
            entry = self.activity_log[invoice_number][index]
        except (ValueError, KeyError, IndexError):
            return

        qr_url = entry.get("qr_url", "")
        if isinstance(qr_url, str) and qr_url.startswith(("http://", "https://")):
            webbrowser.open(qr_url)

    @staticmethod
    def _write(widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="normal")

    def _write_json(self, widget: tk.Text, data: Any) -> None:
        self._write(widget, json.dumps(data, indent=2, ensure_ascii=False))

    @staticmethod
    def _extract_qr_url(response_body: str) -> str | None:
        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError:
            return None

        def find_qr_url(node: Any) -> str | None:
            if isinstance(node, dict):
                qr_value = node.get("qr")
                if isinstance(qr_value, str) and qr_value.startswith(("http://", "https://")):
                    return qr_value
                for value in node.values():
                    found = find_qr_url(value)
                    if found:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = find_qr_url(item)
                    if found:
                        return found
            return None

        return find_qr_url(parsed)


if __name__ == "__main__":
    App().mainloop()
