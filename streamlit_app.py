import streamlit as st
import pandas as pd
import plotly.express as px

# Set page configuration for a wider layout
st.set_page_config(layout="wide")

st.title("🧮 Bulk Stock Comparison Dashboard")

# --- 1. FILE UPLOADING ---
st.sidebar.header("Upload Your Files")
st.sidebar.write("Please upload the two CSV files you want to compare.")

csv_a = st.sidebar.file_uploader("Upload Warehouse CSV", type="csv")
csv_b = st.sidebar.file_uploader("Upload E-Commerce CSV", type="csv")

# --- 2. MAIN APP LOGIC ---
if csv_a and csv_b:
    df_a = pd.read_csv(csv_a)
    df_b = pd.read_csv(csv_b)

    # --- Step 1: Data Preview ---
    st.subheader("Step 1: Preview Your Data")
    st.write("Review the columns and first few rows of your uploaded files to ensure they are correct.")
    
    with st.expander("Warehouse Data Preview"):
        st.write("Columns:", df_a.columns.tolist())
        st.dataframe(df_a.head())

    with st.expander("E-Commerce Data Preview"):
        st.write("Columns:", df_b.columns.tolist())
        st.dataframe(df_b.head())

    st.divider()

    # --- Step 2: Column Mapping ---
    st.subheader("Step 2: Map Your Columns")
    st.write("Select the columns that contain the SKU, Account, and Quantity data from each file.")
    
    col1, col2 = st.columns(2)
    with col1:
        st.info("Warehouse File Mapping", icon="🏢")
        col_sku_a = st.selectbox("Select Warehouse SKU column", df_a.columns, key="sku_a")
        col_acc_a = st.selectbox("Select Warehouse Account column", df_a.columns, key="acc_a")
        col_qty_a = st.selectbox("Select Warehouse Quantity column", df_a.columns, key="qty_a")
    
    with col2:
        st.info("E-Commerce File Mapping", icon="🛒")
        col_sku_b = st.selectbox("Select E-Commerce SKU column", df_b.columns, key="sku_b")
        col_acc_b = st.selectbox("Select E-Commerce Account column", df_b.columns, key="acc_b")
        col_qty_b = st.selectbox("Select E-Commerce Quantity column", df_b.columns, key="qty_b")
    
    st.divider()

    if st.button("🚀 Compare Data", type="primary"):
        try:
            df_a_norm = df_a[[col_sku_a, col_acc_a, col_qty_a]].rename(columns={
                col_sku_a: 'sku', col_acc_a: 'account_number', col_qty_a: 'quantity_warehouse'
            })
            df_b_norm = df_b[[col_sku_b, col_acc_b, col_qty_b]].rename(columns={
                col_sku_b: 'sku', col_acc_b: 'account_number', col_qty_b: 'quantity_ecommerce'
            })

            # Ensure quantity columns are numeric, coercing errors to NaN
            df_a_norm['quantity_warehouse'] = pd.to_numeric(df_a_norm['quantity_warehouse'], errors='coerce')
            df_b_norm['quantity_ecommerce'] = pd.to_numeric(df_b_norm['quantity_ecommerce'], errors='coerce')

            # Drop rows where quantity is not a number after coercion
            df_a_norm.dropna(subset=['quantity_warehouse'], inplace=True)
            df_b_norm.dropna(subset=['quantity_ecommerce'], inplace=True)

            # --- Data Processing ---
            # Perform an inner merge to find common records
            merged_df = pd.merge(df_a_norm, df_b_norm, on=['sku', 'account_number'], how='inner')
            
            # Calculate the difference and determine the status
            merged_df['quantity_difference'] = merged_df['quantity_warehouse'] - merged_df['quantity_ecommerce']
            merged_df['status'] = merged_df['quantity_difference'].apply(lambda x: 'Match' if x == 0 else 'Mismatch')

            # --- Step 4: Display Dashboard & Results ---
            st.subheader("📊 Comparison Dashboard")

            # --- Key Metrics ---
            total_matched = len(merged_df)
            match_count = merged_df['status'].value_counts().get('Match', 0)
            mismatch_count = merged_df['status'].value_counts().get('Mismatch', 0)

            metric1, metric2, metric3 = st.columns(3)
            metric1.metric("Total Matched SKUs", f"{total_matched:,}")
            metric2.metric("Matched Quantities", f"{match_count:,}")
            metric3.metric("❌ Mismatched Quantities", f"{mismatch_count:,}")

            st.divider()
            
            col1, col2 = st.columns([1, 2])
            with col1:
                st.write("**Match vs. Mismatch Breakdown**")
                if total_matched > 0:
                    fig = px.pie(
                        merged_df, 
                        names='status', 
                        title='Comparison Status',
                        color='status',
                        color_discrete_map={'Match': 'lightgreen', 'Mismatch': 'lightcoral'}
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("No matching SKUs found to visualize.")

            with col2:
                st.write("**Stock Status Analysis (for matched items)**")

                wh_in_ecom_out = merged_df[(merged_df['quantity_warehouse'] > 0) & (merged_df['quantity_ecommerce'] == 0)].shape[0]
                ecom_in_wh_out = merged_df[(merged_df['quantity_ecommerce'] > 0) & (merged_df['quantity_warehouse'] == 0)].shape[0]
                in_stock_both = merged_df[(merged_df['quantity_warehouse'] > 0) & (merged_df['quantity_ecommerce'] > 0)].shape[0]

                st.metric("SKUs In-Stock at Warehouse & Out-of-Stock Online", f"{wh_in_ecom_out:,}")
                st.metric("SKUs In-Stock Online & Out-of-Stock at Warehouse", f"{ecom_in_wh_out:,}")
                st.metric("SKUs In-Stock at Both Locations", f"{in_stock_both:,}")


            st.divider()

            # --- Detailed Data Table ---
            st.subheader("📋 Detailed Comparison Results")
            st.dataframe(merged_df)

            # --- Download Button ---
            csv_export = merged_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Results as CSV",
                data=csv_export,
                file_name="stock_comparison_results.csv",
                mime="text/csv",
            )

        except KeyError as e:
            st.error(f"❌ **Column Mapping Error:** A selected column '{e}' was not found. Please check your selections.")
        except Exception as e:
            st.error(f"An unexpected error occurred: {e}")

else:
    st.info("👈 Upload both a Warehouse and an E-Commerce CSV file to begin the comparison process.")
