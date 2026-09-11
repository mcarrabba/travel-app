from datetime import datetime, timedelta
import sqlite3
import pandas as pd
import requests
import streamlit as st

# --- 1. PAGE CONFIG & CUSTOM CSS STYLING ---
st.set_page_config(
    page_title="Travel Schedule & Operations Manager",
    page_icon="✈️",
    layout="wide",
)

st.markdown("""
    <style>
    .main {
        background-color: #f8f9fa;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: #ffffff;
        border-radius: 6px 6px 0px 0px;
        padding: 10px 16px;
        font-weight: 600;
        border: 1px solid #e0e0e0;
    }
    .stTabs [aria-selected="true"] {
        background-color: #1f77b4 !important;
        color: white !important;
    }
    div.stButton > button {
        border-radius: 6px;
        font-weight: 600;
    }
    </style>
""", unsafe_allow_html=True)

# --- 2. DATABASE SETUP ---
DB_NAME = "travel_schedule.db"


def init_db():
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()

  # Create Users Table
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL
        )
    """)

  # Create Appointments Table with travel expense & type fields
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            title TEXT NOT NULL,
            city TEXT NOT NULL,
            phone_number TEXT,
            hotel_name TEXT,
            hotel_location TEXT,
            price REAL,
            expenses REAL,
            parking_expense REAL,
            travel_expense REAL,
            travel_type TEXT,
            notes TEXT,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            buffer_start TEXT NOT NULL,
            buffer_end TEXT NOT NULL
        )
    """)

  # Safe schema migrations if DB already existed
  cursor.execute("PRAGMA table_info(appointments)")
  columns = [info[1] for info in cursor.fetchall()]
  if "username" not in columns:
    cursor.execute(
        "ALTER TABLE appointments ADD COLUMN username TEXT DEFAULT 'default_user'"
    )
  if "phone_number" not in columns:
    cursor.execute("ALTER TABLE appointments ADD COLUMN phone_number TEXT")
  if "hotel_name" not in columns:
    cursor.execute("ALTER TABLE appointments ADD COLUMN hotel_name TEXT")
  if "hotel_location" not in columns:
    cursor.execute("ALTER TABLE appointments ADD COLUMN hotel_location TEXT")
  if "price" not in columns:
    cursor.execute("ALTER TABLE appointments ADD COLUMN price REAL")
  if "expenses" not in columns:
    cursor.execute("ALTER TABLE appointments ADD COLUMN expenses REAL")
  if "parking_expense" not in columns:
    cursor.execute("ALTER TABLE appointments ADD COLUMN parking_expense REAL")
  if "travel_expense" not in columns:
    cursor.execute("ALTER TABLE appointments ADD COLUMN travel_expense REAL")
  if "travel_type" not in columns:
    cursor.execute("ALTER TABLE appointments ADD COLUMN travel_type TEXT")
  if "notes" not in columns:
    cursor.execute("ALTER TABLE appointments ADD COLUMN notes TEXT")

  conn.commit()
  conn.close()


init_db()

# --- 3. LIVE GOOGLE HOTELS API (SerpApi Integration) ---


def get_google_hotel_average_price(city, target_date, api_key=""):
  if not api_key:
    base_price = 140 + (len(city) * 15)
    return round(base_price, 2), "Simulator Mode (Add SerpApi Key for live data)"

  try:
    check_in = target_date
    check_out = (
        datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_hotels",
        "q": f"hotels in {city}",
        "check_in_date": check_in,
        "check_out_date": check_out,
        "currency": "USD",
        "api_key": api_key,
    }

    response = requests.get(url, params=params)
    data = response.json()
    properties = data.get("properties", [])

    prices = []
    for prop in properties:
      rate_info = prop.get("rate_per_night", {})
      lowest = rate_info.get("extracted_lowest")
      if lowest:
        prices.append(float(lowest))

    if prices:
      avg_price = sum(prices) / len(prices)
      return (
          round(avg_price, 2),
          f"Live Google Hotels Data ({len(prices)} hotels analyzed)",
      )
    else:
      return None, "No pricing data found for this city/date combination."
  except Exception as e:
    return None, f"API Error: {str(e)}"


# --- 4. WEATHER & GEOCODING API (Open-Meteo Integration) ---


def get_city_coordinates(city):
  try:
    geo_url = (
        f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
    )
    geo_res = requests.get(geo_url).json()
    if "results" in geo_res and geo_res["results"]:
      lat = geo_res["results"][0]["latitude"]
      lon = geo_res["results"][0]["longitude"]
      return lat, lon
  except Exception:
    pass
  return None, None


def get_destination_weather(city, target_date):
  try:
    lat, lon = get_city_coordinates(city)
    if lat is None or lon is None:
      return None, "City not found for weather lookup."

    geo_url = (
        f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
    )
    geo_res = requests.get(geo_url).json()
    country = geo_res["results"][0].get("country", "")

    weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=temperature_2m_max,temperature_2m_min,precipitation_sum&timezone=auto"
    w_res = requests.get(weather_url).json()

    daily = w_res.get("daily", {})
    dates = daily.get("time", [])

    if target_date in dates:
      idx = dates.index(target_date)
      t_max = daily["temperature_2m_max"][idx]
      t_min = daily["temperature_2m_min"][idx]
      precip = daily["precipitation_sum"][idx]

      t_max_f = round((t_max * 9 / 5) + 32, 1)
      t_min_f = round((t_min * 9 / 5) + 32, 1)

      return {
          "city": f"{city}, {country}",
          "high_c": t_max,
          "low_c": t_min,
          "high_f": t_max_f,
          "low_f": t_min_f,
          "precip": precip,
      }, "Success"
    else:
      return (
          None,
          "Target date is outside the 7-day forecast window (Free weather API"
          " range).",
      )
  except Exception as e:
    return None, f"Weather API Error: {str(e)}"


# --- 5. OVERLAP & BUFFER LOGIC ---


def check_buffer_overlap(username, new_date, new_buf_start, new_buf_end):
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT title, buffer_start, buffer_end FROM appointments WHERE username"
      " = ? AND date = ?",
      (username, new_date),
  )
  existing = cursor.fetchall()
  conn.close()

  new_start_dt = datetime.strptime(f"{new_date} {new_buf_start}", "%Y-%m-%d %H:%M")
  new_end_dt = datetime.strptime(f"{new_date} {new_buf_end}", "%Y-%m-%d %H:%M")

  for title, ext_start, ext_end in existing:
    ext_start_dt = datetime.strptime(
        f"{new_date} {ext_start}", "%Y-%m-%d %H:%M"
    )
    ext_end_dt = datetime.strptime(f"{new_date} {ext_end}", "%Y-%m-%d %H:%M")

    if max(new_start_dt, ext_start_dt) < min(new_end_dt, ext_end_dt):
      return True, title

  return False, None


# --- 6. ICS CALENDAR GENERATOR ---


def generate_ics_file(username):
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT title, city, phone_number, hotel_name, hotel_location, price,"
      " expenses, parking_expense, travel_expense, travel_type, notes, date,"
      " start_time, end_time FROM appointments WHERE username = ?",
      (username,),
  )
  rows = cursor.fetchall()
  conn.close()

  ics_content = [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//Travel Schedule Manager//EN",
  ]

  for row in rows:
    (
        title,
        city,
        phone,
        hotel_name,
        hotel_loc,
        price,
        expenses,
        parking,
        travel_exp,
        travel_type,
        notes,
        date_str,
        start_str,
        end_str,
    ) = row
    dt_start = (
        date_str.replace("-", "") + "T" + start_str.replace(":", "") + "00"
    )
    dt_end = date_str.replace("-", "") + "T" + end_str.replace(":", "") + "00"

    desc = f"City: {city}"
    if phone:
      desc += f"\\nPhone: {phone}"
    if hotel_name:
      desc += f"\\nHotel: {hotel_name} ({hotel_loc or 'No location'})"
    if price:
      desc += f"\\nRevenue: ${price}"

    total_exp = (
        (expenses or 0.0) + (parking or 0.0) + (travel_exp or 0.0)
    )
    if total_exp > 0:
      desc += f"\\nTotal Expenses: ${total_exp}"
      if travel_exp and travel_exp > 0:
        desc += f"\\nTransit ({travel_type}): ${travel_exp}"
      if parking and parking > 0:
        desc += f"\\nParking: ${parking}"
      desc += f"\\nProfit: ${price - total_exp}"

    if notes:
      desc += f"\\nNotes: {notes}"

    ics_content.extend([
        "BEGIN:VEVENT",
        f"SUMMARY:{title}",
        f"DTSTART:{dt_start}",
        f"DTEND:{dt_end}",
        f"LOCATION:{city}",
        f"DESCRIPTION:{desc}",
        "END:VEVENT",
    ])

  ics_content.append("END:VCALENDAR")
  return "\r\n".join(ics_content)


# --- 7. SIDEBAR AUTHENTICATION & SUMMARY PANEL ---
if "logged_in" not in st.session_state:
  st.session_state.logged_in = False
  st.session_state.username = ""

with st.sidebar:
  st.image(
      "https://images.unsplash.com/photo-1488646953014-85cb44e25828?auto=format&fit=crop&w=400&q=80",
      use_container_width=True,
  )
  st.title("✈️ TravelOps Hub")
  st.markdown("---")

  if not st.session_state.logged_in:
    st.subheader("User Portal")
    auth_mode = st.radio("Mode", ["Login", "Register"])

    input_user = st.text_input("Username")
    input_pass = st.text_input("Password", type="password")

    if st.button("Submit"):
      conn = sqlite3.connect(DB_NAME)
      cursor = conn.cursor()

      if auth_mode == "Register":
        try:
          cursor.execute(
              "INSERT INTO users (username, password) VALUES (?, ?)",
              (input_user, input_pass),
          )
          conn.commit()
          st.success("Account created! You can now log in.")
        except sqlite3.IntegrityError:
          st.error("Username already exists.")
      else:
        cursor.execute(
            "SELECT password FROM users WHERE username = ?", (input_user,)
        )
        row = cursor.fetchone()
        if row and row[0] == input_pass:
          st.session_state.logged_in = True
          st.session_state.username = input_user
          st.rerun()
        else:
          st.error("Invalid username or password.")
      conn.close()
  else:
    st.success(f"Logged in as: **{st.session_state.username}**")

    conn = sqlite3.connect(DB_NAME)
    sidebar_df = pd.read_sql_query(
        "SELECT price, expenses, parking_expense, travel_expense FROM"
        " appointments WHERE username = ?",
        conn,
        params=(st.session_state.username,),
    )
    conn.close()

    total_appts_count = len(sidebar_df)
    if total_appts_count > 0:
      sidebar_df["price"] = sidebar_df["price"].fillna(0)
      sidebar_df["expenses"] = sidebar_df["expenses"].fillna(0)
      sidebar_df["parking_expense"] = sidebar_df["parking_expense"].fillna(0)
      sidebar_df["travel_expense"] = sidebar_df["travel_expense"].fillna(0)
      total_costs = (
          sidebar_df["expenses"]
          + sidebar_df["parking_expense"]
          + sidebar_df["travel_expense"]
      ).sum()
      sidebar_net = sidebar_df["price"].sum() - total_costs
    else:
      sidebar_net = 0.0

    st.metric(label="Total Scheduled Events", value=total_appts_count)
    st.metric(label="Estimated Net Profit", value=f"${sidebar_net:,.2f}")
    st.markdown("---")

    if st.button("Log Out"):
      st.session_state.logged_in = False
      st.session_state.username = ""
      st.rerun()

  st.caption("Protected by 30-min buffer blackout logic.")


# --- 8. MAIN STREAMLIT UI DESIGN ---

st.title("🌍 Travel Schedule & Operations Manager")

if not st.session_state.logged_in:
  st.warning(
      "⚠️ Please **Log In** or **Register** using the sidebar on the left to"
      " manage your travel schedule and budget."
  )
else:
  current_user = st.session_state.username

  st.write(
      f"Welcome back, **{current_user}**! Streamline your travel with"
      " automated **30-minute buffer blackouts**, transit expense tracking,"
      " and profit calculations."
  )

  tab1, tab2, tab3, tab4 = st.tabs([
      "📅 Schedule & Add Appointment",
      "💰 Budget & Profit Dashboard",
      "🗺️ Interactive Travel Map",
      "🏨 Hotel Prices & Weather",
  ])

  # --- TAB 1: SCHEDULE MANAGEMENT ---
  with tab1:
    top_col1, top_col2 = st.columns([3, 1])
    with top_col1:
      st.subheader("Your Travel Itinerary")
    with top_col2:
      if "show_form" not in st.session_state:
        st.session_state.show_form = True

      if st.button("➕ New Appointment", use_container_width=True):
        st.session_state.show_form = not st.session_state.show_form

    if st.session_state.show_form:
      with st.expander("📝 Create New Appointment Form", expanded=True):
        with st.form("appointment_form"):
          f_col1, f_col2 = st.columns(2)
          with f_col1:
            title = st.text_input("Appointment Title", "Client Meeting")
            city = st.text_input("City", "New York")
            phone_number = st.text_input("Phone Number", "555-0199")
            hotel_name = st.text_input("Hotel Name", "The Plaza Hotel")
            hotel_location = st.text_input(
                "Hotel Address / Location", "768 5th Ave, New York, NY"
            )
          with f_col2:
            price = st.number_input(
                "Price / Revenue ($)", min_value=0.0, value=0.0, step=10.0
            )
            expenses = st.number_input(
                "General Expenses ($)", min_value=0.0, value=0.0, step=10.0
            )
            parking_expense = st.number_input(
                "Parking Expense ($)", min_value=0.0, value=0.0, step=5.0
            )
            travel_type = st.selectbox(
                "Travel / Transit Type", ["Car", "Train", "Flight"]
            )
            travel_expense = st.number_input(
                "Travel Expense Cost ($)", min_value=0.0, value=0.0, step=10.0
            )
            appt_date = st.date_input("Date")

          c_time1, c_time2 = st.columns(2)
          with c_time1:
            start_time_input = st.time_input(
                "Start Time", value=datetime.strptime("10:00", "%H:%M").time()
            )
          with c_time2:
            end_time_input = st.time_input(
                "End Time", value=datetime.strptime("11:30", "%H:%M").time()
            )

          notes = st.text_area(
              "Notes / Remarks",
              placeholder=(
                  "Add confirmation codes, meeting agenda, or details here..."
              ),
          )

          submitted = st.form_submit_button("Save Appointment & Apply Buffers")

          if submitted:
            date_str = appt_date.strftime("%Y-%m-%d")
            start_str = start_time_input.strftime("%H:%M")
            end_str = end_time_input.strftime("%H:%M")

            start_dt = datetime.strptime(
                f"{date_str} {start_str}", "%Y-%m-%d %H:%M"
            )
            end_dt = datetime.strptime(f"{date_str} {end_str}", "%Y-%m-%d %H:%M")

            if start_dt >= end_dt:
              st.error("Error: End time must be later than start time.")
            else:
              buf_start_dt = start_dt - timedelta(minutes=30)
              buf_end_dt = end_dt + timedelta(minutes=30)

              buf_start_str = buf_start_dt.strftime("%H:%M")
              buf_end_str = buf_end_dt.strftime("%H:%M")

              has_overlap, conflicting_title = check_buffer_overlap(
                  current_user, date_str, buf_start_str, buf_end_str
              )

              if has_overlap:
                st.error(
                    f"❌ Conflict! This overlaps with the 30-minute buffer of"
                    f" '{conflicting_title}'."
                )
              else:
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute(
                    """
                                        INSERT INTO appointments (username, title, city, phone_number, hotel_name, hotel_location, price, expenses, parking_expense, travel_expense, travel_type, notes, date, start_time, end_time, buffer_start, buffer_end)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                    (
                        current_user,
                        title,
                        city,
                        phone_number,
                        hotel_name,
                        hotel_location,
                        price,
                        expenses,
                        parking_expense,
                        travel_expense,
                        travel_type,
                        notes,
                        date_str,
                        start_str,
                        end_str,
                        buf_start_str,
                        buf_end_str,
                    ),
                )
                conn.commit()
                conn.close()
                st.success(
                    f"✅ Appointment saved! Blocked out buffer from"
                    f" {buf_start_str} to {buf_end_str}."
                )
                st.session_state.show_form = False
                st.rerun()

    st.divider()

    # Display Itinerary Table
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query(
        "SELECT id, title, city, hotel_name, hotel_location, phone_number,"
        " price, expenses, parking_expense, travel_type, travel_expense, notes,"
        " date, start_time, end_time, buffer_start, buffer_end FROM"
        " appointments WHERE username = ? ORDER BY date, start_time",
        conn,
        params=(current_user,),
    )
    conn.close()

    if df.empty:
      st.info(
          "No appointments added yet. Click **'➕ New Appointment'** above to"
          " get started!"
      )
    else:
      # Combine all expenses for total cost calculation
      df["total_expenses"] = (
          df["expenses"].fillna(0)
          + df["parking_expense"].fillna(0)
          + df["travel_expense"].fillna(0)
      )
      df["profit"] = df["price"].fillna(0) - df["total_expenses"]

      st.dataframe(df, use_container_width=True)

      col_act1, col_act2 = st.columns([1, 1])
      with col_act1:
        ics_data = generate_ics_file(current_user)
        st.download_button(
            label="📥 Export Calendar (.ics)",
            data=ics_data,
            file_name=f"{current_user}_schedule.ics",
            mime="text/calendar",
        )

      with col_act2:
        st.markdown("### Manage Records")
        delete_id = st.number_input(
            "Enter Appointment ID to Delete", min_value=0, step=1
        )
        if st.button("Delete Appointment"):
          conn = sqlite3.connect(DB_NAME)
          cursor = conn.cursor()
          cursor.execute(
              "DELETE FROM appointments WHERE id = ? AND username = ?",
              (delete_id, current_user),
          )
          conn.commit()
          conn.close()
          st.success(f"Deleted appointment ID {delete_id}")
          st.rerun()

  # --- TAB 2: BUDGET & PROFIT DASHBOARD ---
  with tab2:
    st.subheader("📊 Financial, Budget & Profit Dashboard")
    conn = sqlite3.connect(DB_NAME)
    budget_df = pd.read_sql_query(
        "SELECT city, price, expenses, parking_expense, travel_type,"
        " travel_expense FROM appointments WHERE username = ?",
        conn,
        params=(current_user,),
    )
    conn.close()

    if budget_df.empty:
      st.info(
          "No appointments found. Add revenue and expense data to view"
          " financial breakdowns!"
      )
    else:
      budget_df["price"] = budget_df["price"].fillna(0)
      budget_df["expenses"] = budget_df["expenses"].fillna(0)
      budget_df["parking_expense"] = budget_df["parking_expense"].fillna(0)
      budget_df["travel_expense"] = budget_df["travel_expense"].fillna(0)

      # Sum total expenses (general + parking + travel)
      budget_df["combined_expenses"] = (
          budget_df["expenses"]
          + budget_df["parking_expense"]
          + budget_df["travel_expense"]
      )
      budget_df["profit"] = budget_df["price"] - budget_df["combined_expenses"]

      total_revenue = budget_df["price"].sum()
      total_general_exp = budget_df["expenses"].sum()
      total_parking_exp = budget_df["parking_expense"].sum()
      total_travel_exp = budget_df["travel_expense"].sum()
      total_expenses = (
          total_general_exp + total_parking_exp + total_travel_exp
      )
      total_profit = budget_df["profit"].sum()

      col_b1, col_b2, col_b3, col_b4, col_b5 = st.columns(5)
      with col_b1:
        st.metric(label="Total Revenue ($)", value=f"${total_revenue:,.2f}")
      with col_b2:
        st.metric(
            label="General Expenses ($)", value=f"${total_general_exp:,.2f}"
        )
      with col_b3:
        st.metric(label="Parking ($)", value=f"${total_parking_exp:,.2f}")
      with col_b4:
        st.metric(label="Transit ($)", value=f"${total_travel_exp:,.2f}")
      with col_b5:
        st.metric(
            label="Net Profit ($)",
            value=f"${total_profit:,.2f}",
            delta=(
                "Profitable"
                if total_profit >= 0
                else "Operating at a Loss"
            ),
        )

      st.write("### Financial Breakdown by City")
      city_summary = (
          budget_df.groupby("city")[
              [
                  "price",
                  "expenses",
                  "parking_expense",
                  "travel_expense",
                  "combined_expenses",
                  "profit",
              ]
          ]
          .sum()
          .reset_index()
      )
      city_summary.columns = [
          "City",
          "Total Revenue ($)",
          "General Expenses ($)",
          "Parking Expenses ($)",
          "Travel Expenses ($)",
          "Total Expenses ($)",
          "Net Profit ($)",
      ]
      st.dataframe(city_summary, use_container_width=True)

  # --- TAB 3: INTERACTIVE TRAVEL MAP ---
  with tab3:
    st.subheader("🗺️ Interactive Travel Itinerary Map")
    st.write(
        "Visualizing all your scheduled destination cities across your trip."
    )

    conn = sqlite3.connect(DB_NAME)
    map_df = pd.read_sql_query(
        "SELECT title, city, date, start_time FROM appointments WHERE username"
        " = ?",
        conn,
        params=(current_user,),
    )
    conn.close()

    if map_df.empty:
      st.info(
          "No appointments scheduled yet. Add a destination city to view it on"
          " the map!"
      )
    else:
      cities = map_df["city"].unique()
      city_coords = {}
      for c in cities:
        lat, lon = get_city_coordinates(c)
        if lat and lon:
          city_coords[c] = {"lat": lat, "lon": lon}

      map_df["latitude"] = map_df["city"].map(
          lambda x: city_coords.get(x, {}).get("lat")
      )
      map_df["longitude"] = map_df["city"].map(
          lambda x: city_coords.get(x, {}).get("lon")
      )

      valid_map_df = map_df.dropna(subset=["latitude", "longitude"])

      if valid_map_df.empty:
        st.warning("Could not map the current cities. Please check city spelling.")
      else:
        st.map(
            valid_map_df,
            latitude="latitude",
            longitude="longitude",
            size=30,
            zoom=4,
        )
        st.caption(
            "Pins represent your appointment cities extracted automatically"
            " from your schedule."
        )

  # --- TAB 4: HOTEL PRICES & WEATHER ---
  with tab4:
    st.subheader("🏨 Hotel Pricing & 🌤️ Destination Weather")
    st.write(
        "Check real-time nightly hotel rates via Google Hotels and look up"
        " weather forecasts for your trip dates."
    )

    serpapi_key = st.text_input(
        "SerpApi Key (Optional - leave blank for hotel pricing simulator)",
        type="password",
    )

    h_col1, h_col2 = st.columns(2)
    with h_col1:
      search_city = st.text_input("Destination City for Search", "New York")
    with h_col2:
      search_date = st.date_input("Check-in / Date", value=datetime.today())

    if st.button("Fetch Hotel Prices & Weather Forecast"):
      date_str = search_date.strftime("%Y-%m-%d")

      with st.spinner(f"Checking hotel rates in {search_city}..."):
        avg_price, status_msg = get_google_hotel_average_price(
            search_city, date_str, serpapi_key
        )
        if avg_price is not None:
          st.metric(
              label=f"Avg Hotel Price in {search_city}",
              value=f"${avg_price:.2f} / night",
          )
        else:
          st.warning("Could not fetch exact hotel average.")
        st.caption(f"Hotel Source: {status_msg}")

      st.divider()

      with st.spinner(f"Fetching weather forecast for {search_city}..."):
        weather_data, w_status = get_destination_weather(search_city, date_str)
        if weather_data:
          st.success(
              f"🌤️ Weather Forecast for {weather_data['city']} on {date_str}:"
          )
          w_col1, w_col2, w_col3 = st.columns(3)
          with w_col1:
            st.metric(
                label="High Temperature",
                value=(
                    f"{weather_data['high_f']}°F ({weather_data['high_c']}°C)"
                ),
            )
          with w_col2:
            st.metric(
                label="Low Temperature",
                value=f"{weather_data['low_f']}°F ({weather_data['low_c']}°C)",
            )
          with w_col3:
            st.metric(
                label="Expected Precipitation",
                value=f"{weather_data['precip']} mm",
            )
        else:
          st.info(
              f"Note on weather: {w_status} (Try picking a date within the next"
              " 7 days for live forecast data)."
          )
