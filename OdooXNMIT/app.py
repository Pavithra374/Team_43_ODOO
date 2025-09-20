import os
from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector
from dotenv import load_dotenv
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from datetime import datetime

load_dotenv()

app = Flask(__name__)

app.secret_key = 'your_super_secret_key_change_this'
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- Database Connection and User Loader (No Changes) ---
def get_db_connection():
    conn = mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )
    return conn

class User(UserMixin):
    def __init__(self, id, name, email):
        self.id = id
        self.name = name
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user_data = cursor.fetchone()
    cursor.close()
    conn.close()
    if user_data:
        return User(id=user_data['id'], name=user_data['name'], email=user_data['email'])
    return None

# --- Main Routes and Helpers (No Changes) ---
@app.route('/')
def index():
    return redirect(url_for('list_manufacturing_orders'))

def check_component_availability(cursor, orders):
    product_stock = {}
    cursor.execute("SELECT id, on_hand_quantity FROM products")
    for row in cursor.fetchall():
        product_stock[row['id']] = row['on_hand_quantity']

    for order in orders:
        order['component_status'] = 'Available'
        cursor.execute("SELECT bc.component_product_id, bc.quantity_required FROM bom_components bc WHERE bc.bom_id = %s", (order['bom_id'],))
        components = cursor.fetchall()
        
        if not components:
            order['component_status'] = 'N/A'
            continue

        for comp in components:
            required = comp['quantity_required'] * order['quantity_to_produce']
            if product_stock.get(comp['component_product_id'], 0) < required:
                order['component_status'] = 'Not Available'
                break
    return orders

def log_mo_status_change(cursor, mo_id, status):
    cursor.execute(
        "INSERT INTO manufacturing_order_status_history (mo_id, status, timestamp) VALUES (%s, %s, %s)",
        (mo_id, status, datetime.now())
    )

def get_mo_data_for_json(cursor, mo_id):
    """Helper function to get all MO data for JSON responses"""
    cursor.execute("""
        SELECT mo.*, p.name AS product_name, b.name AS bom_name, u.name AS assignee_name
        FROM manufacturing_orders mo
        JOIN products p ON mo.product_id = p.id
        LEFT JOIN boms b ON mo.bom_id = b.id
        LEFT JOIN users u ON mo.assignee_id = u.id
        WHERE mo.id = %s
    """, (mo_id,))
    order = cursor.fetchone()
    
    cursor.execute("""
        SELECT p.name AS component_name, p.on_hand_quantity, (bc.quantity_required * mo.quantity_to_produce) AS to_consume
        FROM manufacturing_orders mo
        JOIN bom_components bc ON mo.bom_id = bc.bom_id
        JOIN products p ON bc.component_product_id = p.id
        WHERE mo.id = %s
    """, (mo_id,))
    components = cursor.fetchall()
    for comp in components:
        comp['availability_status'] = 'Available' if comp['on_hand_quantity'] >= comp['to_consume'] else 'Not Available'
    
    cursor.execute("""
        SELECT wo.*, wc.name AS work_center_name
        FROM work_orders wo
        JOIN work_centers wc ON wo.work_center_id = wc.id
        WHERE wo.mo_id = %s
    """, (mo_id,))
    work_orders = cursor.fetchall()
    
    cursor.execute(
        "SELECT status, timestamp FROM manufacturing_order_status_history WHERE mo_id = %s ORDER BY timestamp",
        (mo_id,)
    )
    status_history = cursor.fetchall()
    
    return {
        'order': order,
        'components': components,
        'work_orders': work_orders,
        'status_history': status_history
    }

# --- REPLACED PRODUCT ROUTES ---
@app.route('/products')
@login_required
def list_products():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM products')
    products = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('products.html', products=products)

# Make sure to import jsonify at the top of your app.py file
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

# ADD THIS NEW API ROUTE
@app.route('/api/manufacturing-orders')
@login_required
def api_manufacturing_orders():
    # This code is copied and adapted from your list_manufacturing_orders function
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    active_filter = request.args.get('filter', 'All')
    search_query = request.args.get('search', '')
    filter_owner = request.args.get('owner', 'all')
    
    base_query = "SELECT mo.id, mo.schedule_start_date, mo.quantity_to_produce, mo.status, mo.bom_id, p.name as product_name FROM manufacturing_orders mo JOIN products p ON mo.product_id = p.id"
    where_clauses = []
    params = []
    
    if filter_owner == 'my':
        where_clauses.append("mo.assignee_id = %s")
        params.append(current_user.id)
    if active_filter in ['Draft', 'Confirmed', 'In Progress', 'Done']:
        where_clauses.append("mo.status = %s")
        params.append(active_filter)
    elif active_filter == 'Late':
        where_clauses.append("mo.schedule_start_date < CURDATE() AND mo.status = 'Confirmed'")
    elif active_filter == 'Not Assigned':
        where_clauses.append("mo.assignee_id IS NULL")
    
    if search_query:
        where_clauses.append("(p.name LIKE %s OR mo.status LIKE %s OR mo.id LIKE %s)")
        search_term = f"%{search_query}%"
        params.extend([search_term, search_term, search_query.replace('MO-', '')])

    if where_clauses:
        base_query += " WHERE " + " AND ".join(where_clauses)
    base_query += " ORDER BY mo.schedule_start_date DESC"
    
    cursor.execute(base_query, tuple(params))
    manufacturing_orders = cursor.fetchall()
    
    manufacturing_orders = check_component_availability(cursor, manufacturing_orders)

    cursor.close()
    conn.close()

    # Convert date objects to strings for JSON compatibility
    for order in manufacturing_orders:
        if order.get('schedule_start_date'):
            order['schedule_start_date'] = order['schedule_start_date'].strftime('%Y-%m-%d')

    # Instead of rendering a template, return the data as JSON
    return jsonify(manufacturing_orders)

@app.route('/products/<int:product_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    if request.method == 'POST':
        # --- This part handles SAVING the form ---
        name = request.form['name']
        description = request.form['description']
        min_stock = request.form['min_stock_level']
        reorder_qty = request.form['reorder_quantity']
        
        cursor.execute("""
            UPDATE products 
            SET name = %s, description = %s, min_stock_level = %s, reorder_quantity = %s
            WHERE id = %s
        """, (name, description, min_stock, reorder_qty, product_id))
        
        conn.commit()
        flash(f"Product '{name}' updated successfully.", 'success')
        cursor.close()
        conn.close()
        return redirect(url_for('list_products'))
    
    # --- This part handles SHOWING the form ---
    cursor.execute("SELECT * FROM products WHERE id = %s", (product_id,))
    product = cursor.fetchone()
    cursor.close()
    conn.close()
    
    return render_template('product_edit_form.html', product=product)

# NEW: Replaces the old 'add_product' function
@app.route('/products/update', methods=['GET', 'POST'])
@login_required
def update_stock():
    if request.method == 'POST':
        product_name = request.form['name']
        quantity_change = int(request.form['quantity_change'])
        description = request.form.get('description', '')
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM products WHERE LOWER(name) = LOWER(%s)", (product_name,))
        product = cursor.fetchone()

        if product:
            product_id = product['id']
            new_quantity = product['on_hand_quantity'] + quantity_change
            if new_quantity < 0:
                flash(f"Error: Cannot remove {abs(quantity_change)} units. Only {product['on_hand_quantity']} units of {product_name} are in stock.", 'error')
                return redirect(url_for('list_products'))
            cursor.execute("UPDATE products SET on_hand_quantity = %s WHERE id = %s", (new_quantity, product_id))
            reason = "Manual Stock Addition" if quantity_change > 0 else "Manual Stock Removal"
            cursor.execute("INSERT INTO stock_ledger (product_id, quantity_change, reason) VALUES (%s, %s, %s)", (product_id, quantity_change, reason))
            flash(f"Updated stock for {product_name}. New quantity: {new_quantity}", 'success')
        elif quantity_change > 0:
            cursor.execute('INSERT INTO products (name, description, on_hand_quantity) VALUES (%s, %s, %s)', (product_name, description, quantity_change))
            product_id = cursor.lastrowid
            cursor.execute("INSERT INTO stock_ledger (product_id, quantity_change, reason) VALUES (%s, %s, %s)", (product_id, quantity_change, "Initial Stock"))
            flash(f"New product '{product_name}' created with {quantity_change} units.", 'success')
        else:
             flash(f"Error: Cannot remove stock from '{product_name}' because it does not exist.", 'error')

        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('list_products'))
    return render_template('update_stock_form.html')

# NEW: Function to delete a product
@app.route('/products/<int:product_id>/delete', methods=['POST'])
@login_required
def delete_product(product_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
        conn.commit()
        flash("Product deleted successfully.", 'success')
    except mysql.connector.Error as err:
        flash("Error: Cannot delete this product because it is being used in a Bill of Materials or a Manufacturing Order.", 'error')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('list_products'))

# --- All other routes (Work Centers, BOMs, MOs, etc.) remain the same ---
# (The rest of the file is unchanged)
@app.route('/work-centers')
@login_required
def list_work_centers():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM work_centers')
    work_centers = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('work_centers.html', work_centers=work_centers)

@app.route('/work-centers/add', methods=['GET', 'POST'])
@login_required
def add_work_center():
    if request.method == 'POST':
        name = request.form['name']
        cost = request.form['cost_per_hour']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO work_centers (name, cost_per_hour) VALUES (%s, %s)',
                       (name, cost))
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('list_work_centers'))
    return render_template('work_center_form.html')

@app.route('/boms')
@login_required
def list_boms():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT b.id, b.name AS bom_name, p.name AS product_name
        FROM boms b
        JOIN products p ON b.product_id = p.id
    """)
    boms = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('boms.html', boms=boms)

@app.route('/boms/add', methods=['GET', 'POST'])
@login_required
def add_bom():
    if request.method == 'POST':
        name = request.form['name']
        product_id = request.form['product_id']
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO boms (name, product_id) VALUES (%s, %s)', (name, product_id))
        new_bom_id = cursor.lastrowid
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('bom_detail', bom_id=new_bom_id))
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM products')
    products = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('bom_form.html', products=products)

@app.route('/boms/<int:bom_id>')
@login_required
def bom_detail(bom_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT b.id, b.name AS bom_name, p.name AS product_name FROM boms b JOIN products p ON b.product_id = p.id WHERE b.id = %s", (bom_id,))
    bom = cursor.fetchone()
    cursor.execute("SELECT bc.quantity_required, p.name AS component_name FROM bom_components bc JOIN products p ON bc.component_product_id = p.id WHERE bc.bom_id = %s", (bom_id,))
    components = cursor.fetchall()
    cursor.execute("SELECT bo.name AS operation_name, bo.duration_minutes, wc.name AS work_center_name FROM bom_operations bo JOIN work_centers wc ON bo.work_center_id = wc.id WHERE bo.bom_id = %s", (bom_id,))
    operations = cursor.fetchall()
    cursor.execute('SELECT * FROM products')
    all_products = cursor.fetchall()
    cursor.execute('SELECT * FROM work_centers')
    all_work_centers = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('bom_detail.html', bom=bom, components=components, operations=operations, all_products=all_products, all_work_centers=all_work_centers)

@app.route('/boms/<int:bom_id>/add_component', methods=['POST'])
@login_required
def add_component_to_bom(bom_id):
    product_id = request.form['product_id']
    quantity = request.form['quantity']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO bom_components (bom_id, component_product_id, quantity_required) VALUES (%s, %s, %s)',
                   (bom_id, product_id, quantity))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('bom_detail', bom_id=bom_id))

@app.route('/boms/<int:bom_id>/add_operation', methods=['POST'])
@login_required
def add_operation_to_bom(bom_id):
    operation_name = request.form['operation_name']
    work_center_id = request.form['work_center_id']
    duration = request.form['duration']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO bom_operations (bom_id, name, work_center_id, duration_minutes) VALUES (%s, %s, %s, %s)',
                   (bom_id, operation_name, work_center_id, duration))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('bom_detail', bom_id=bom_id))

@app.route('/manufacturing-orders')
@login_required
def list_manufacturing_orders():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    kpi_counts = {}
    statuses = ['Draft', 'Confirmed', 'In Progress', 'Done']
    for status in statuses:
        cursor.execute("SELECT COUNT(*) as count FROM manufacturing_orders WHERE status = %s", (status,))
        kpi_counts[status] = cursor.fetchone()['count']
    cursor.execute("SELECT COUNT(*) as count FROM manufacturing_orders")
    kpi_counts['All'] = cursor.fetchone()['count']
    cursor.execute("SELECT COUNT(*) as count FROM manufacturing_orders WHERE schedule_start_date < CURDATE() AND status = 'Confirmed'")
    kpi_counts['Late'] = cursor.fetchone()['count']
    cursor.execute("SELECT COUNT(*) as count FROM manufacturing_orders WHERE assignee_id IS NULL")
    kpi_counts['Not Assigned'] = cursor.fetchone()['count']
    my_kpi_counts = {}
    for status in ['Confirmed', 'In Progress', 'Done']:
         cursor.execute("SELECT COUNT(*) as count FROM manufacturing_orders WHERE status = %s AND assignee_id = %s", (status, current_user.id))
         my_kpi_counts[status] = cursor.fetchone()['count']
    cursor.execute("SELECT COUNT(*) as count FROM manufacturing_orders WHERE schedule_start_date < CURDATE() AND status = 'Confirmed' AND assignee_id = %s", (current_user.id,))
    my_kpi_counts['Late'] = cursor.fetchone()['count']
    active_filter = request.args.get('filter', 'All')
    search_query = request.args.get('search', '')
    filter_owner = request.args.get('owner', 'all')
    base_query = "SELECT mo.id, mo.schedule_start_date, mo.quantity_to_produce, mo.status, mo.bom_id, p.name as product_name FROM manufacturing_orders mo JOIN products p ON mo.product_id = p.id"
    where_clauses = []
    params = []
    if filter_owner == 'my':
        where_clauses.append("mo.assignee_id = %s")
        params.append(current_user.id)
    if active_filter in statuses:
        where_clauses.append("mo.status = %s")
        params.append(active_filter)
    elif active_filter == 'Late':
        where_clauses.append("mo.schedule_start_date < CURDATE() AND mo.status = 'Confirmed'")
    elif active_filter == 'Not Assigned':
        where_clauses.append("mo.assignee_id IS NULL")
    if search_query:
        where_clauses.append("(p.name LIKE %s OR mo.status LIKE %s OR mo.id LIKE %s)")
        search_term = f"%{search_query}%"
        params.extend([search_term, search_term, search_query.replace('MO-', '')])
    if where_clauses:
        base_query += " WHERE " + " AND ".join(where_clauses)
    base_query += " ORDER BY mo.schedule_start_date DESC"
    cursor.execute(base_query, tuple(params))
    manufacturing_orders = cursor.fetchall()
    manufacturing_orders = check_component_availability(cursor, manufacturing_orders)
    cursor.close()
    conn.close()
    return render_template('dashboard.html', manufacturing_orders=manufacturing_orders, kpi_counts=kpi_counts, my_kpi_counts=my_kpi_counts, active_filter=active_filter, filter_owner=filter_owner, search_query=search_query)

@app.route('/manufacturing-orders/add', methods=['GET', 'POST'])
@login_required
def add_manufacturing_order():
    if request.method == 'POST':
        product_id = request.form['product_id']
        quantity = request.form['quantity']
        bom_id = request.form['bom_id']
        schedule_start_date = request.form['schedule_start_date']
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            'INSERT INTO manufacturing_orders (product_id, quantity_to_produce, bom_id, status, schedule_start_date, assignee_id) VALUES (%s, %s, %s, %s, %s, %s)',
            (product_id, quantity, bom_id, 'Draft', schedule_start_date, current_user.id)
        )
        mo_id = cursor.lastrowid
        log_mo_status_change(cursor, mo_id, 'Draft')
        cursor.execute('SELECT * FROM bom_operations WHERE bom_id = %s', (bom_id,))
        operations = cursor.fetchall()
        for op in operations:
            cursor.execute(
                'INSERT INTO work_orders (mo_id, operation_name, work_center_id, status, duration_minutes) VALUES (%s, %s, %s, %s, %s)',
                (mo_id, op['name'], op['work_center_id'], 'To Do', op['duration_minutes'])
            )
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('list_manufacturing_orders'))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute('SELECT * FROM products')
    products = cursor.fetchall()
    cursor.execute('SELECT b.id, b.name, p.name as product_name FROM boms b JOIN products p ON b.product_id = p.id')
    boms = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('mo_form.html', products=products, boms=boms)



@app.route('/manufacturing-orders/<int:mo_id>')
@login_required
def mo_detail(mo_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT mo.*, p.name AS product_name, b.name AS bom_name, u.name AS assignee_name
        FROM manufacturing_orders mo
        JOIN products p ON mo.product_id = p.id
        LEFT JOIN boms b ON mo.bom_id = b.id
        LEFT JOIN users u ON mo.assignee_id = u.id
        WHERE mo.id = %s
    """, (mo_id,))
    order = cursor.fetchone()
    cursor.execute("""
        SELECT p.name AS component_name, p.on_hand_quantity, (bc.quantity_required * mo.quantity_to_produce) AS to_consume
        FROM manufacturing_orders mo
        JOIN bom_components bc ON mo.bom_id = bc.bom_id
        JOIN products p ON bc.component_product_id = p.id
        WHERE mo.id = %s
    """, (mo_id,))
    components = cursor.fetchall()
    for comp in components:
        comp['availability_status'] = 'Available' if comp['on_hand_quantity'] >= comp['to_consume'] else 'Not Available'
    cursor.execute("""
        SELECT wo.*, wc.name AS work_center_name
        FROM work_orders wo
        JOIN work_centers wc ON wo.work_center_id = wc.id
        WHERE wo.mo_id = %s
    """, (mo_id,))
    work_orders = cursor.fetchall()
    all_wos_done = all(wo['status'] == 'Done' for wo in work_orders) if work_orders else False
    if all_wos_done and order['status'] == 'In Progress':
        cursor.execute("UPDATE manufacturing_orders SET status = 'To Close' WHERE id = %s", (mo_id,))
        log_mo_status_change(cursor, mo_id, 'To Close')
        conn.commit()
        order['status'] = 'To Close'
    cursor.execute(
        "SELECT status, timestamp FROM manufacturing_order_status_history WHERE mo_id = %s ORDER BY timestamp",
        (mo_id,)
    )
    status_history = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('mo_detail.html', order=order, components=components, work_orders=work_orders,status_history=status_history)

@app.route('/manufacturing-orders/<int:mo_id>/confirm', methods=['POST'])
@login_required
def confirm_manufacturing_order(mo_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("UPDATE manufacturing_orders SET status = 'Confirmed' WHERE id = %s", (mo_id,))
    log_mo_status_change(cursor, mo_id, 'Confirmed')
    conn.commit()
    
    # Get updated order data for JSON response
    cursor.execute("""
        SELECT mo.*, p.name AS product_name, b.name AS bom_name, u.name AS assignee_name
        FROM manufacturing_orders mo
        JOIN products p ON mo.product_id = p.id
        LEFT JOIN boms b ON mo.bom_id = b.id
        LEFT JOIN users u ON mo.assignee_id = u.id
        WHERE mo.id = %s
    """, (mo_id,))
    order = cursor.fetchone()
    
    cursor.execute("""
        SELECT p.name AS component_name, p.on_hand_quantity, (bc.quantity_required * mo.quantity_to_produce) AS to_consume
        FROM manufacturing_orders mo
        JOIN bom_components bc ON mo.bom_id = bc.bom_id
        JOIN products p ON bc.component_product_id = p.id
        WHERE mo.id = %s
    """, (mo_id,))
    components = cursor.fetchall()
    for comp in components:
        comp['availability_status'] = 'Available' if comp['on_hand_quantity'] >= comp['to_consume'] else 'Not Available'
    
    cursor.execute("""
        SELECT wo.*, wc.name AS work_center_name
        FROM work_orders wo
        JOIN work_centers wc ON wo.work_center_id = wc.id
        WHERE wo.mo_id = %s
    """, (mo_id,))
    work_orders = cursor.fetchall()
    
    cursor.execute(
        "SELECT status, timestamp FROM manufacturing_order_status_history WHERE mo_id = %s ORDER BY timestamp",
        (mo_id,)
    )
    status_history = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Check if this is an AJAX request by looking for X-Requested-With header
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'order': order,
            'components': components,
            'work_orders': work_orders,
            'status_history': status_history
        })
    
    return redirect(url_for('mo_detail', mo_id=mo_id))

@app.route('/manufacturing-orders/<int:mo_id>/start', methods=['POST'])
@login_required
def start_manufacturing_order(mo_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "UPDATE manufacturing_orders SET status = 'In Progress', start_time = %s WHERE id = %s AND start_time IS NULL",
        (datetime.now(), mo_id)
    )
    log_mo_status_change(cursor, mo_id, 'In Progress')
    conn.commit()
    
    # Get updated order data for JSON response
    cursor.execute("""
        SELECT mo.*, p.name AS product_name, b.name AS bom_name, u.name AS assignee_name
        FROM manufacturing_orders mo
        JOIN products p ON mo.product_id = p.id
        LEFT JOIN boms b ON mo.bom_id = b.id
        LEFT JOIN users u ON mo.assignee_id = u.id
        WHERE mo.id = %s
    """, (mo_id,))
    order = cursor.fetchone()
    
    cursor.execute("""
        SELECT p.name AS component_name, p.on_hand_quantity, (bc.quantity_required * mo.quantity_to_produce) AS to_consume
        FROM manufacturing_orders mo
        JOIN bom_components bc ON mo.bom_id = bc.bom_id
        JOIN products p ON bc.component_product_id = p.id
        WHERE mo.id = %s
    """, (mo_id,))
    components = cursor.fetchall()
    for comp in components:
        comp['availability_status'] = 'Available' if comp['on_hand_quantity'] >= comp['to_consume'] else 'Not Available'
    
    cursor.execute("""
        SELECT wo.*, wc.name AS work_center_name
        FROM work_orders wo
        JOIN work_centers wc ON wo.work_center_id = wc.id
        WHERE wo.mo_id = %s
    """, (mo_id,))
    work_orders = cursor.fetchall()
    
    cursor.execute(
        "SELECT status, timestamp FROM manufacturing_order_status_history WHERE mo_id = %s ORDER BY timestamp",
        (mo_id,)
    )
    status_history = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Check if this is an AJAX request by looking for X-Requested-With header
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'order': order,
            'components': components,
            'work_orders': work_orders,
            'status_history': status_history
        })
    
    return redirect(url_for('mo_detail', mo_id=mo_id))

@app.route('/manufacturing-orders/<int:mo_id>/cancel', methods=['POST'])
@login_required
def cancel_manufacturing_order(mo_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("UPDATE manufacturing_orders SET status = 'Cancelled' WHERE id = %s", (mo_id,))
    log_mo_status_change(cursor, mo_id, 'Cancelled')
    conn.commit()
    
    # Get updated order data for JSON response
    cursor.execute("""
        SELECT mo.*, p.name AS product_name, b.name AS bom_name, u.name AS assignee_name
        FROM manufacturing_orders mo
        JOIN products p ON mo.product_id = p.id
        LEFT JOIN boms b ON mo.bom_id = b.id
        LEFT JOIN users u ON mo.assignee_id = u.id
        WHERE mo.id = %s
    """, (mo_id,))
    order = cursor.fetchone()
    
    cursor.execute("""
        SELECT p.name AS component_name, p.on_hand_quantity, (bc.quantity_required * mo.quantity_to_produce) AS to_consume
        FROM manufacturing_orders mo
        JOIN bom_components bc ON mo.bom_id = bc.bom_id
        JOIN products p ON bc.component_product_id = p.id
        WHERE mo.id = %s
    """, (mo_id,))
    components = cursor.fetchall()
    for comp in components:
        comp['availability_status'] = 'Available' if comp['on_hand_quantity'] >= comp['to_consume'] else 'Not Available'
    
    cursor.execute("""
        SELECT wo.*, wc.name AS work_center_name
        FROM work_orders wo
        JOIN work_centers wc ON wo.work_center_id = wc.id
        WHERE wo.mo_id = %s
    """, (mo_id,))
    work_orders = cursor.fetchall()
    
    cursor.execute(
        "SELECT status, timestamp FROM manufacturing_order_status_history WHERE mo_id = %s ORDER BY timestamp",
        (mo_id,)
    )
    status_history = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Check if this is an AJAX request by looking for X-Requested-With header
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'order': order,
            'components': components,
            'work_orders': work_orders,
            'status_history': status_history
        })
    
    return redirect(url_for('mo_detail', mo_id=mo_id))

@app.route('/work-orders/<int:wo_id>/start-timer', methods=['POST'])
@login_required
def start_work_order_timer(wo_id):
    mo_id = request.form['mo_id']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("UPDATE work_orders SET start_time = %s, status = 'In Progress' WHERE id = %s", (datetime.now(), wo_id))
    cursor.execute("SELECT status FROM manufacturing_orders WHERE id = %s", (mo_id,))
    mo_status = cursor.fetchone()['status']
    if mo_status == 'Confirmed':
        cursor.execute(
            "UPDATE manufacturing_orders SET status = 'In Progress', start_time = %s WHERE id = %s AND start_time IS NULL",
            (datetime.now(), mo_id)
        )
        log_mo_status_change(cursor, mo_id, 'In Progress')
    conn.commit()
    
    # Get updated order data for JSON response
    cursor.execute("""
        SELECT mo.*, p.name AS product_name, b.name AS bom_name, u.name AS assignee_name
        FROM manufacturing_orders mo
        JOIN products p ON mo.product_id = p.id
        LEFT JOIN boms b ON mo.bom_id = b.id
        LEFT JOIN users u ON mo.assignee_id = u.id
        WHERE mo.id = %s
    """, (mo_id,))
    order = cursor.fetchone()
    
    cursor.execute("""
        SELECT p.name AS component_name, p.on_hand_quantity, (bc.quantity_required * mo.quantity_to_produce) AS to_consume
        FROM manufacturing_orders mo
        JOIN bom_components bc ON mo.bom_id = bc.bom_id
        JOIN products p ON bc.component_product_id = p.id
        WHERE mo.id = %s
    """, (mo_id,))
    components = cursor.fetchall()
    for comp in components:
        comp['availability_status'] = 'Available' if comp['on_hand_quantity'] >= comp['to_consume'] else 'Not Available'
    
    cursor.execute("""
        SELECT wo.*, wc.name AS work_center_name
        FROM work_orders wo
        JOIN work_centers wc ON wo.work_center_id = wc.id
        WHERE wo.mo_id = %s
    """, (mo_id,))
    work_orders = cursor.fetchall()
    
    cursor.execute(
        "SELECT status, timestamp FROM manufacturing_order_status_history WHERE mo_id = %s ORDER BY timestamp",
        (mo_id,)
    )
    status_history = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Check if this is an AJAX request by looking for X-Requested-With header
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'order': order,
            'components': components,
            'work_orders': work_orders,
            'status_history': status_history
        })
    
    return redirect(url_for('mo_detail', mo_id=mo_id))

@app.route('/work-orders/<int:wo_id>/done', methods=['POST'])
@login_required
def complete_work_order(wo_id):
    mo_id = request.form['mo_id']
    end_time = datetime.now()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT start_time FROM work_orders WHERE id = %s", (wo_id,))
    work_order = cursor.fetchone()
    start_time = work_order['start_time']
    real_duration = 0
    if start_time:
        duration_seconds = (end_time - start_time).total_seconds()
        real_duration = round(duration_seconds / 60)
    cursor.execute(
        "UPDATE work_orders SET end_time = %s, real_duration_minutes = %s, status = 'Done' WHERE id = %s",
        (end_time, real_duration, wo_id)
    )
    
    # Check if all work orders are done and update MO status
    cursor.execute("SELECT status FROM work_orders WHERE mo_id = %s", (mo_id,))
    work_orders_statuses = [row['status'] for row in cursor.fetchall()]
    all_wos_done = all(status == 'Done' for status in work_orders_statuses)
    
    if all_wos_done:
        cursor.execute("SELECT status FROM manufacturing_orders WHERE id = %s", (mo_id,))
        current_mo_status = cursor.fetchone()['status']
        if current_mo_status == 'In Progress':
            cursor.execute("UPDATE manufacturing_orders SET status = 'To Close' WHERE id = %s", (mo_id,))
            log_mo_status_change(cursor, mo_id, 'To Close')
    
    conn.commit()
    
    # Get updated order data for JSON response
    cursor.execute("""
        SELECT mo.*, p.name AS product_name, b.name AS bom_name, u.name AS assignee_name
        FROM manufacturing_orders mo
        JOIN products p ON mo.product_id = p.id
        LEFT JOIN boms b ON mo.bom_id = b.id
        LEFT JOIN users u ON mo.assignee_id = u.id
        WHERE mo.id = %s
    """, (mo_id,))
    order = cursor.fetchone()
    
    cursor.execute("""
        SELECT p.name AS component_name, p.on_hand_quantity, (bc.quantity_required * mo.quantity_to_produce) AS to_consume
        FROM manufacturing_orders mo
        JOIN bom_components bc ON mo.bom_id = bc.bom_id
        JOIN products p ON bc.component_product_id = p.id
        WHERE mo.id = %s
    """, (mo_id,))
    components = cursor.fetchall()
    for comp in components:
        comp['availability_status'] = 'Available' if comp['on_hand_quantity'] >= comp['to_consume'] else 'Not Available'
    
    cursor.execute("""
        SELECT wo.*, wc.name AS work_center_name
        FROM work_orders wo
        JOIN work_centers wc ON wo.work_center_id = wc.id
        WHERE wo.mo_id = %s
    """, (mo_id,))
    work_orders = cursor.fetchall()
    
    cursor.execute(
        "SELECT status, timestamp FROM manufacturing_order_status_history WHERE mo_id = %s ORDER BY timestamp",
        (mo_id,)
    )
    status_history = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Check if this is an AJAX request by looking for X-Requested-With header
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'order': order,
            'components': components,
            'work_orders': work_orders,
            'status_history': status_history
        })
    
    return redirect(url_for('mo_detail', mo_id=mo_id))
    
@app.route('/work-orders')
@login_required
def list_work_orders():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    search_query = request.args.get('search', '')
    base_query = """
        SELECT 
            wo.id, wo.operation_name, wo.duration_minutes, wo.real_duration_minutes,
            wo.status, wc.name as work_center_name, p.name as finished_product_name,
            mo.id as mo_id, wo.start_time, wo.end_time
        FROM work_orders wo
        JOIN work_centers wc ON wo.work_center_id = wc.id
        JOIN manufacturing_orders mo ON wo.mo_id = mo.id
        JOIN products p ON mo.product_id = p.id
    """
    params = []
    if search_query:
        base_query += " WHERE (wo.operation_name LIKE %s OR wc.name LIKE %s OR p.name LIKE %s OR wo.status LIKE %s)"
        search_term = f"%{search_query}%"
        params.extend([search_term, search_term, search_term, search_term])
    base_query += " ORDER BY wo.id DESC"
    cursor.execute(base_query, tuple(params))
    work_orders = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('work_orders_list.html', work_orders=work_orders, search_query=search_query)

@app.route('/manufacturing-orders/<int:mo_id>/produce', methods=['POST'])
@login_required
def produce_manufacturing_order(mo_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT status FROM manufacturing_orders WHERE id = %s", (mo_id,))
    current_status = cursor.fetchone()['status']
    if current_status != 'To Close':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': 'Order is not ready to be produced'}), 400
        return redirect(url_for('mo_detail', mo_id=mo_id))
    
    cursor.execute("SELECT product_id, quantity_to_produce, bom_id FROM manufacturing_orders WHERE id = %s", (mo_id,))
    mo = cursor.fetchone()
    cursor.execute("""
        SELECT p.id, p.name, p.on_hand_quantity, p.min_stock_level, p.reorder_quantity, bc.quantity_required 
        FROM bom_components bc
        JOIN products p ON bc.component_product_id = p.id
        WHERE bc.bom_id = %s
    """, (mo['bom_id'],))
    components = cursor.fetchall()
    for comp in components:
        consumed_qty = comp['quantity_required'] * mo['quantity_to_produce']
        new_stock_level = comp['on_hand_quantity'] - consumed_qty
        cursor.execute("UPDATE products SET on_hand_quantity = %s WHERE id = %s", (new_stock_level, comp['id']))
        cursor.execute("INSERT INTO stock_ledger (product_id, quantity_change, reason, mo_id) VALUES (%s, %s, %s, %s)", (comp['id'], -consumed_qty, 'MO Consumption', mo_id))
        if new_stock_level < comp['min_stock_level']:
            reorder_amount = comp['reorder_quantity']
            flash(f"LOW STOCK ALERT: {comp['name']} fell to {new_stock_level}. Automatically reordering {reorder_amount} units.", 'warning')
            cursor.execute("UPDATE products SET on_hand_quantity = on_hand_quantity + %s WHERE id = %s", (reorder_amount, comp['id']))
            cursor.execute("INSERT INTO stock_ledger (product_id, quantity_change, reason, mo_id) VALUES (%s, %s, %s, %s)", (comp['id'], reorder_amount, 'Automatic Reorder', mo_id))
    produced_qty = mo['quantity_to_produce']
    cursor.execute("UPDATE products SET on_hand_quantity = on_hand_quantity + %s WHERE id = %s", (produced_qty, mo['product_id']))
    cursor.execute("INSERT INTO stock_ledger (product_id, quantity_change, reason, mo_id) VALUES (%s, %s, %s, %s)", (mo['product_id'], produced_qty, 'MO Production', mo_id))
    cursor.execute("UPDATE manufacturing_orders SET status = 'Done', completed_at = %s WHERE id = %s", (datetime.now(), mo_id))
    log_mo_status_change(cursor, mo_id, 'Done')
    conn.commit()
    
    # Get updated order data for JSON response
    cursor.execute("""
        SELECT mo.*, p.name AS product_name, b.name AS bom_name, u.name AS assignee_name
        FROM manufacturing_orders mo
        JOIN products p ON mo.product_id = p.id
        LEFT JOIN boms b ON mo.bom_id = b.id
        LEFT JOIN users u ON mo.assignee_id = u.id
        WHERE mo.id = %s
    """, (mo_id,))
    order = cursor.fetchone()
    
    cursor.execute("""
        SELECT p.name AS component_name, p.on_hand_quantity, (bc.quantity_required * mo.quantity_to_produce) AS to_consume
        FROM manufacturing_orders mo
        JOIN bom_components bc ON mo.bom_id = bc.bom_id
        JOIN products p ON bc.component_product_id = p.id
        WHERE mo.id = %s
    """, (mo_id,))
    components = cursor.fetchall()
    for comp in components:
        comp['availability_status'] = 'Available' if comp['on_hand_quantity'] >= comp['to_consume'] else 'Not Available'
    
    cursor.execute("""
        SELECT wo.*, wc.name AS work_center_name
        FROM work_orders wo
        JOIN work_centers wc ON wo.work_center_id = wc.id
        WHERE wo.mo_id = %s
    """, (mo_id,))
    work_orders = cursor.fetchall()
    
    cursor.execute(
        "SELECT status, timestamp FROM manufacturing_order_status_history WHERE mo_id = %s ORDER BY timestamp",
        (mo_id,)
    )
    status_history = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Check if this is an AJAX request by looking for X-Requested-With header
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'order': order,
            'components': components,
            'work_orders': work_orders,
            'status_history': status_history
        })
    
    return redirect(url_for('mo_detail', mo_id=mo_id))

@app.route('/stock-ledger')
@login_required
def stock_ledger():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT s.timestamp, s.quantity_change, s.reason, p.name as product_name
        FROM stock_ledger s
        JOIN products p ON s.product_id = p.id
        ORDER BY s.timestamp DESC
    """)
    ledger_entries = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('stock_ledger.html', entries=ledger_entries)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user_data = cursor.fetchone()
        cursor.close()
        conn.close()
        if user_data and bcrypt.check_password_hash(user_data['password_hash'], password):
            user = User(id=user_data['id'], name=user_data['name'], email=user_data['email'])
            login_user(user)
            return redirect(url_for('list_manufacturing_orders'))
        else:
            return 'Invalid username or password'
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                (name, email, hashed_password, 'Manager')
            )
            conn.commit()
        except mysql.connector.Error as err:
            return "Error: Could not register user. Email might already exist."
        finally:
            cursor.close()
            conn.close()
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)