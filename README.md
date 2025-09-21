# ODOOxNMIT Manufacturing Management App

A modular manufacturing management app that digitizes the entire “order-to-output” workflow. It lets businesses create, track, and manage manufacturing orders, work orders, stock, and BOMs in one place, with dashboards, reports, and real-time inventory updates.

## Features

- User authentication (signup/login/logout)
- Dashboard with KPIs and filters for manufacturing orders
- Product master: add, edit, delete, and update stock
- Work centers management
- Bill of Materials (BOM) creation and editing
- Manufacturing order creation, tracking, and status history
- Work order management and progress tracking
- Stock ledger for inventory movements
- Automatic low stock alerts and reorder logic

## Tech Stack

- Python (Flask)
- MySQL
- Flask-Login, Flask-Bcrypt
- HTML/CSS (PicoCSS), Jinja2 templates

## Setup

1. Clone the repository.
2. Install dependencies:
    ```sh
    pip install -r requirements.txt
    ```
3. Set up a MySQL database and configure `.env` with:
    ```
    DB_HOST=your_host
    DB_USER=your_user
    DB_PASSWORD=your_password
    DB_NAME=your_db
    ```
4. Run the app:
    ```sh
    python [app.py](http://_vscodecontentref_/1)
    ```
5. Access at [http://localhost:5000](http://localhost:5000)

## Folder Structure

- `OdooXNMIT/app.py` – Main Flask app
- `OdooXNMIT/templates/` – HTML templates


Demo video :- https://drive.google.com/file/d/1DNBt2cSyE4cb6mHOF1VEwpvFyd9Y_EBc/view?usp=sharing
