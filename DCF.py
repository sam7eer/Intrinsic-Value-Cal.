import streamlit as st
import yfinance as yf
import pandas as pd

# --- Page Configuration ---
st.set_page_config(page_title="Intrinsic Value Calculator", page_icon="📈")


# --- Helper Functions ---

def format_large_number(num):
    """
    Formats a large number into a human-readable string (e.g., 1.23B, 456.7M).
    """
    if abs(num) >= 1_000_000_000:
        return f"{num / 1_000_000_000:,.2f}B"
    elif abs(num) >= 1_000_000:
        return f"{num / 1_000_000:,.2f}M"
    else:
        return f"{num:,.2f}"

def get_financial_data(ticker_symbol):
    """
    Fetches required financial data from Yahoo Finance.
    Falls back to Net Profit if Owner Earnings are negative.
    """
    try:
        stock = yf.Ticker(ticker_symbol)
        
        # Fetch financial statements
        cashflow = stock.cashflow
        income_stmt = stock.income_stmt
        balance_sheet = stock.balance_sheet
        
        if cashflow.empty or income_stmt.empty or balance_sheet.empty:
            return {"error": "Could not fetch complete financial statements."}

        # --- Calculate Owner Earnings (The Buffett Way) ---
        net_income = income_stmt.loc['Net Income'].iloc[0]
        depreciation = cashflow.loc['Depreciation And Amortization'].iloc[0]
        # We assume Maintenance Capex is equal to Depreciation
        maintenance_capex = depreciation 
        owner_earnings = net_income + depreciation - maintenance_capex
        
        # --- ** NEW LOGIC: Fallback to Net Profit ** ---
        if owner_earnings > 0:
            starting_cash_flow = owner_earnings
            metric_used = "Owner Earnings"
        else:
            starting_cash_flow = net_income
            metric_used = "Net Profit (as Owner Earnings were negative)"
            
        # Get historical FCF for growth calculation (we still use FCF for growth trend)
        fcf_data = cashflow.loc['Free Cash Flow'].iloc[:4]

        # Get company info
        info = stock.info
        shares_outstanding = info.get('sharesOutstanding')
        
        total_debt = balance_sheet.loc['Total Debt'].iloc[0] if 'Total Debt' in balance_sheet.index else 0
        cash_and_equivalents = balance_sheet.loc['Cash And Cash Equivalents'].iloc[0] if 'Cash And Cash Equivalents' in balance_sheet.index else 0
        
        current_price = stock.history(period='1d')['Close'].iloc[0]
        
        if not all([pd.notna(starting_cash_flow), pd.notna(shares_outstanding), pd.notna(current_price)]):
             return {"error": "Missing critical financial data from the API."}

        return {
            "fcf_data": fcf_data,
            "starting_cash_flow": starting_cash_flow,
            "metric_used": metric_used,
            "shares_outstanding": shares_outstanding,
            "total_debt": total_debt,
            "cash_and_equivalents": cash_and_equivalents,
            "current_price": current_price,
            "long_name": info.get('longName', ticker_symbol),
            "currency": info.get('currency', 'USD')
        }
    except Exception as e:
        return {"error": f"An error occurred fetching data for {ticker_symbol}."}

def calculate_historical_growth(fcf_data):
    """Calculates the average historical FCF growth rate."""
    fcf_data = fcf_data.iloc[::-1]
    growth_rates = fcf_data.pct_change().dropna()
    if not growth_rates.empty:
        growth_rates = growth_rates[growth_rates < 2] 
        avg_growth = growth_rates.mean()
        return avg_growth
    return 0.05

def run_dcf_model(data, growth_rate, discount_rate, period=5, perpetual_rate=0.025):
    """
    Runs a two-stage DCF model for a specified period (5 or 10 years).
    """
    cash_flow = data['starting_cash_flow'] # Uses the determined starting cash flow
    shares = data['shares_outstanding']
    debt = data['total_debt']
    cash = data['cash_and_equivalents']

    future_cash_flows = []
    # Loop for the selected period (5 or 10 years)
    for i in range(1, period + 1):
        projected_cf = cash_flow * ((1 + growth_rate) ** i)
        future_cash_flows.append(projected_cf)

    last_projected_cf = future_cash_flows[-1]
    terminal_value = (last_projected_cf * (1 + perpetual_rate)) / (discount_rate - perpetual_rate)

    discounted_cf = [cf / ((1 + discount_rate) ** (i+1)) for i, cf in enumerate(future_cash_flows)]
    discounted_terminal_value = terminal_value / ((1 + discount_rate) ** period)

    total_pv_cf = sum(discounted_cf) + discounted_terminal_value
    equity_value = total_pv_cf - debt + cash
    
    intrinsic_value = equity_value / shares
    return intrinsic_value

# --- Streamlit App UI ---

st.title("📈 Intrinsic Value Calculator")
st.markdown("""
Know the intrinsic value of a company before you invest. This tool uses a Discounted Cash Flow (DCF) model 
to estimate a company's value, a method famously championed by **Warren Buffett**.
""")

st.header("1. Enter Company Details")
ticker_input = st.text_input("Enter the Stock Ticker Symbol (e.g., 'AAPL', 'RELIANCE')", "PLTR").upper()

if ticker_input:
    # --- Auto-detect Indian stocks silently ---
    data = get_financial_data(ticker_input)
    
    if "error" in data:
        final_ticker = f"{ticker_input}.NS"
        data = get_financial_data(final_ticker)
    
    if "error" in data:
        st.error("Sorry, could not find data for that ticker. Please check the symbol and try again.")
    else:
        historical_growth = calculate_historical_growth(data['fcf_data'])
        
        st.header("2. Set Your Assumptions")
        st.write(f"Now showing assumptions for **{data['long_name']}**.")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            default_growth_rate = max(0.0, round(historical_growth * 100, 2))
            user_growth_rate = st.number_input(
                f"Future Growth Rate (%)", 
                min_value=0.0, value=8.0, step=0.5,
                help=f"Based on the last 4 years, the FCF growth trend was {historical_growth:.2%}. You can adjust this."
            )

        with col2:
            projection_period = st.radio(
                "Projection Period",
                ('5 Years', '10 Years'),
                horizontal=True,
                help="Select the high-growth period for the DCF model."
            )

        with col3:
            user_discount_rate = st.number_input(
                "Your Expected Return (%)",
                min_value=0.0, value=11.0, step=0.5,
                help="This is the discount rate (WACC). A common default is 12%."
            )

        growth_rate_decimal = user_growth_rate / 100
        discount_rate_decimal = user_discount_rate / 100
        period_in_years = 10 if projection_period == '10 Years' else 5
        
        if st.button("Calculate Intrinsic Value", type="primary"):
            currency_symbol = "₹" if data['currency'] == 'INR' else "$"
            
            # --- ** FIX APPLIED HERE ** ---
            # Display which cash flow metric was used, with correct currency formatting.
            if data['currency'] == 'INR':
                display_value = f"₹{data['starting_cash_flow']/10**7:,.2f} Cr"
            else:
                display_value = f"${format_large_number(data['starting_cash_flow'])}"

            st.markdown(f"""
            *Starting cash flow metric used: **{data['metric_used']}***<br>
            *Value: **{display_value}***
            """, unsafe_allow_html=True)


            intrinsic_value = run_dcf_model(data, growth_rate_decimal, discount_rate_decimal, period=period_in_years)
            current_price = data['current_price']
            
            st.header("3. Results")
            col1, col2 = st.columns(2)
            
            col1.metric("Calculated Intrinsic Value", f"{currency_symbol}{intrinsic_value:,.2f}")
            col2.metric("Current Market Price", f"{currency_symbol}{current_price:,.2f}")
            
            margin_of_safety = ((intrinsic_value - current_price) / current_price)
            
            if margin_of_safety > 0:
                st.markdown(f"""
                <div style="background-color: #28a745; color: white; padding: 10px; border-radius: 5px;">
                    <h4>Verdict: Undervalued by {margin_of_safety:.2%} 🎉</h4>
                    <p>Looks like a potential opportunity! The stock is trading below its calculated intrinsic value.</p>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="background-color: #dc3545; color: white; padding: 10px; border-radius: 5px;">
                    <h4>Verdict: Overvalued by {margin_of_safety:.2%} ⚠️</h4>
                    <p>Invest with caution. The stock is trading above its calculated intrinsic value.</p>
                </div>
                """, unsafe_allow_html=True)
            with st.expander("A Note on Model Limitations"):
                st.write("""
                This standard DCF model is most reliable for established companies with a history of stable, positive cash flows. 
                
                For certain types of companies, especially high-growth tech firms, the valuation can be less accurate due to factors not explicitly modeled here, such as:
                - **Stock-Based Compensation (SBC):** High SBC can depress Net Income, leading to an artificially low "Owner Earnings" calculation and a lower valuation.
                - **Heavy Reinvestment:** Companies aggressively investing for future growth may show low or negative free cash flow, even if they are fundamentally healthy.
                
                For these cases, a more specialized valuation model may be required.
                """)

            
            st.info("""
            **Disclaimer:** This is not financial advice. The calculated value is highly dependent on the 
            assumptions you provide. Always do your own thorough research.
            """)

