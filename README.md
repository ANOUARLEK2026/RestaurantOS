# RestaurantOS 2.2 — JavaScript + Python + SQLite

RestaurantOS is a local-first restaurant management system designed for desktop use, with optional LAN binding through environment variables.

## Included modules

- Secure local login with PBKDF2 password hashing and expiring in-memory sessions
- Dashboard with live sales, orders, expenses, tables and stock alerts
- POS with menu categories, tax calculation, customer assignment, discounts and table assignment
- Order workflow: open → preparing → ready → paid/cancelled, with kitchen board and table release
- Automatic table release when an order is closed
- Menu management: add, edit, archive
- Inventory: stock levels, minimum levels, movements, waste/adjustments
- Menu recipes with automatic ingredient consumption when an order is paid
- Purchasing: suppliers, receiving stock, invoice references
- Expenses: operating costs separated from purchases
- Payments: cash/card/transfer/other, order settlement and payment history
- Reports: sales, COGS, gross/operating profit, expenses, purchases, payment mix, top items, CSV export and printing
- Settings: restaurant identity, currency, tax and receipt footer
- Admin staff account creation with role selection and real password authentication
- SQLite database backups with timestamped files
- Audit log, customer CRM, reservations, cash register sessions and receivables/payables debts
- PWA shell, responsive UI, mouse/touch/keyboard support and optional sound feedback
- Unified UI theme tokens with tighter spacing, improved dark/light contrast, consistent controls, focus states and reduced grid stretching

## Run on Windows

1. Install Python 3.10 or newer.
2. Double-click `START-RESTAURANTOS.bat`.
3. The application opens at `http://127.0.0.1:4000/`.

For optional LAN access, run the server with `RESTAURANTOS_HOST=0.0.0.0` and expose port 4000 only on a trusted private network.

No Node.js, npm, Docker or PostgreSQL is required.

## Demo administrator

Email: `admin@restaurantos.local`
Password: `Admin@123`

The application stores the password as a salted PBKDF2-SHA256 hash. Change the demo password before using the software operationally.

## Smoke test

Start the server, then run:

```text
python tests_smoke_test.py
```

The test creates an order, moves it through the kitchen workflow and settles it by payment.

## Project structure

```text
RestaurantOS-JS-PY-SQL/
├── backend/
│   └── server.py
├── database/
│   ├── schema.sql
│   ├── seed.sql
│   ├── restaurantos.db
│   └── backups/
├── frontend/
│   └── index.html
├── static/
│   ├── app.js
│   ├── app.css
│   ├── manifest.json
│   ├── sw.js
│   └── images/
├── logs/
├── tests_smoke_test.py
└── START-RESTAURANTOS.bat
```
