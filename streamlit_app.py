
import streamlit as st
import pandas as pd
import plotly.express as px
import time # To measure processing time

st.set_page_config(layout="wide")

st.title("Stock Comparison Dashboard")

# --- Helper function to format file size ---
def format_bytes(size_bytes):
    """Converts bytes to a human-readable format (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} Bytes"
    elif size_bytes < 1024**2:
        return f"{size_bytes/1024:.2f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes/1024**2:.2f} MB"
    else:
        return f"{size_bytes/1024**3:.2f} GB"

# --- 1. FILE UPLOADING ---
st.sidebar.header("Upload Your Files")
st.sidebar.write("Please upload the two CSV files you want to compare.")

csv_a = st.sidebar.file_uploader("Upload Warehouse CSV", type="csv")
csv_b = st.sidebar.file_uploader("Upload E-Commerce CSV", type="csv")

# --- 2. MAIN APP LOGIC ---
if csv_a and csv_b:
    df_a = pd.read_csv(csv_a)
    df_b = pd.read_csv(csv_b)

    # --- Step 1: Data Preview & File Info ---
    st.subheader("Step 1: Preview Your Data & File Info")
    st.write("Review the columns, first few rows, and size of your uploaded files.")
    
    col1, col2 = st.columns(2)
    with col1:
        with st.expander(f"Warehouse Data: **{csv_a.name}**", expanded=True):
            st.metric("File Size", format_bytes(csv_a.size))
            st.write("Columns:", df_a.columns.tolist())
            st.dataframe(df_a.head())

    with col2:
        with st.expander(f"E-Commerce Data: **{csv_b.name}**", expanded=True):
            st.metric("File Size", format_bytes(csv_b.size))
            st.write("Columns:", df_b.columns.tolist())
            st.dataframe(df_b.head())

    st.divider()

    # --- Step 2: Column Mapping ---
    st.subheader("Step 2: Map Your Columns")
    st.write("Select the columns that contain the SKU, Account, and Quantity data from each file.")
    
    map_col1, map_col2 = st.columns(2)
    with map_col1:
        st.info("Warehouse File Mapping", icon="🏢")
        col_sku_a = st.selectbox("Select Warehouse SKU column", df_a.columns, key="sku_a")
        col_acc_a = st.selectbox("Select Warehouse Account column", df_a.columns, key="acc_a")
        col_qty_a = st.selectbox("Select Warehouse Quantity column", df_a.columns, key="qty_a")
    
    with map_col2:
        st.info("E-Commerce File Mapping", icon="🛒")
        col_sku_b = st.selectbox("Select E-Commerce SKU column", df_b.columns, key="sku_b")
        col_acc_b = st.selectbox("Select E-Commerce Account column", df_b.columns, key="acc_b")
        col_qty_b = st.selectbox("Select E-Commerce Quantity column", df_b.columns, key="qty_b")
    
    st.divider()

    # --- Step 3: Run Comparison ---
    if st.button("Compare Data", type="primary"):
        start_time = time.time()
        try:
            # --- Data Normalization ---
            # Ensure quantity columns are numeric, coercing errors to NaN and filling others
            df_a[col_qty_a] = pd.to_numeric(df_a[col_qty_a], errors='coerce')
            df_b[col_qty_b] = pd.to_numeric(df_b[col_qty_b], errors='coerce')
            
            # Drop rows where the essential quantity is not a number
            df_a.dropna(subset=[col_qty_a], inplace=True)
            df_b.dropna(subset=[col_qty_b], inplace=True)

            # Normalize column names for merging
            df_a_norm = df_a[[col_sku_a, col_acc_a, col_qty_a]].rename(columns={
                col_sku_a: 'sku', col_acc_a: 'account_number', col_qty_a: 'quantity_warehouse'
            })
            df_b_norm = df_b[[col_sku_b, col_acc_b, col_qty_b]].rename(columns={
                col_sku_b: 'sku', col_acc_b: 'account_number', col_qty_b: 'quantity_ecommerce'
            })
            
            # --- Data Processing ---
            merged_df = pd.merge(df_a_norm, df_b_norm, on=['sku', 'account_number'], how='inner')
            merged_df['quantity_difference'] = merged_df['quantity_warehouse'] - merged_df['quantity_ecommerce']
            merged_df['status'] = merged_df['quantity_difference'].apply(lambda x: 'Match' if x == 0 else 'Mismatch')
            
            end_time = time.time()
            processing_time = end_time - start_time

            # --- Step 4: Display Dashboard & Results ---
            st.header("📊 Comparison Dashboard")

            # --- Key Metrics ---
            total_matched = len(merged_df)
            match_count = merged_df['status'].value_counts().get('Match', 0)
            mismatch_count = merged_df['status'].value_counts().get('Mismatch', 0)

            metric1, metric2, metric3, metric4 = st.columns(4)
            metric1.metric("Total Matched SKUs", f"{total_matched:,}")
            metric2.metric("✅ Matched Quantities", f"{match_count:,}")
            metric3.metric("❌ Mismatched Quantities", f"{mismatch_count:,}")
            metric4.metric("Processing Time", f"{processing_time:.2f} sec")
            
            st.divider()
            
            # --- Visuals and Detailed Analytics ---
            st.subheader("📈 Detailed Analytics")
            viz_col, analysis_col = st.columns([1, 2])
            
            with viz_col:
                st.write("**Match vs. Mismatch Breakdown**")
                if total_matched > 0:
                    fig = px.pie(
                        merged_df, names='status', title='Comparison Status',
                        color='status', color_discrete_map={'Match': 'lightgreen', 'Mismatch': 'lightcoral'}
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("No matching SKUs found.")

            with analysis_col:
                analysis1, analysis2 = st.columns(2)
                with analysis1:
                    st.write("**Stock Status Analysis**")
                    wh_in_ecom_out = merged_df[(merged_df['quantity_warehouse'] > 0) & (merged_df['quantity_ecommerce'] == 0)].shape[0]
                    ecom_in_wh_out = merged_df[(merged_df['quantity_ecommerce'] > 0) & (merged_df['quantity_warehouse'] == 0)].shape[0]
                    in_stock_both = merged_df[(merged_df['quantity_warehouse'] > 0) & (merged_df['quantity_ecommerce'] > 0)].shape[0]
                    
                    st.metric("SKUs In-Stock at Warehouse Only", f"{wh_in_ecom_out:,}")
                    st.metric("SKUs In-Stock Online Only", f"{ecom_in_wh_out:,}")
                    st.metric("SKUs In-Stock at Both Locations", f"{in_stock_both:,}")
                
                with analysis2:
                    st.write("**Quantity Analysis**")
                    total_qty_a = merged_df['quantity_warehouse'].sum()
                    total_qty_b = merged_df['quantity_ecommerce'].sum()
                    total_discrepancy = abs(merged_df['quantity_difference']).sum()
                    
                    st.metric("Total Warehouse Quantity", f"{int(total_qty_a):,}")
                    st.metric("Total E-Commerce Quantity", f"{int(total_qty_b):,}")
                    st.metric("Total Absolute Discrepancy", f"{int(total_discrepancy):,}")

            st.divider()

            # --- Detailed Data Table ---
            st.subheader("📋 Detailed Comparison Results")
            st.dataframe(merged_df)

            # --- Download Button ---
            csv_export = merged_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Results as CSV",
                data=csv_export, file_name="stock_comparison_results.csv", mime="text/csv",
            )

        except KeyError as e:
            st.error(f"❌ **Column Mapping Error:** A selected column '{e}' was not found. Please check your selections.")
        except Exception as e:
            st.error(f"An unexpected error occurred: {e}")

else:
    st.info("👈 Upload both a Warehouse and an E-Commerce CSV file to begin the comparison process.")
