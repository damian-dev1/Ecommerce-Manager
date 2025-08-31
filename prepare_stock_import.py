import os
import csv
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import create_engine, Column, String, Integer

DATABASE_URL = "sqlite:///database_name.db"
OUTPUT_DIRECTORY = ""
CHUNK_SIZE = 1000

Base = declarative_base()
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

class Inventory(Base):
    __tablename__ = 'inventory'
    Account = Column(String, nullable=False)
    SupplierSKU = Column(String, primary_key=True)
    FreeStock = Column(Integer, nullable=True)

class InventoryLatest(Base):
    __tablename__ = 'inventory_latest'
    Account = Column(String, nullable=False)
    SupplierSKU = Column(String, primary_key=True)
    FreeStock = Column(Integer, nullable=True)

def prepare_magento2_import(output_directory, chunk_size):
    """Prepare data for Magento 2 stock import where changes are detected and split into multiple files."""
    print("Preparing Magento 2 stock import files for changed SKUs...")

    stmt = (
        select(InventoryLatest.SupplierSKU, InventoryLatest.FreeStock, Inventory.FreeStock.label('PreviousFreeStock'))
        .outerjoin(Inventory, Inventory.SupplierSKU == InventoryLatest.SupplierSKU)
    )
    results = session.execute(stmt).all()

    changes = [(sku, free_stock) for sku, free_stock, previous_stock in results if free_stock != previous_stock]

    if not changes:
        print("No changes detected. No files generated.")
        return

    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    current_date = datetime.now().strftime('%Y%m%d')

    file_counter = 1
    row_counter = 0

    output_file_path = os.path.join(output_directory, f"m2_stock_import_{current_date}_{file_counter}.csv")
    output_file = open(output_file_path, mode="w", newline="")
    writer = csv.writer(output_file)

    writer.writerow(["sku", "stock_status", "source_code", "qty"])

    try:
        for sku, free_stock in changes:
            stock_status = 1 if free_stock > 0 else 0

            writer.writerow([sku, stock_status, "pos_337", free_stock])
            writer.writerow([sku, stock_status, "src_virtualstock", free_stock])
            row_counter += 2

            if row_counter >= chunk_size:
                output_file.close()

                file_counter += 1
                row_counter = 0

                output_file_path = os.path.join(output_directory, f"m2_stock_import_{current_date}_{file_counter}.csv")
                output_file = open(output_file_path, mode="w", newline="")
                writer = csv.writer(output_file)

                writer.writerow(["sku", "stock_status", "source_code", "qty"])

        print(f"Magento 2 stock import files generated in: {output_directory}")
    finally:
        output_file.close()

if __name__ == "__main__":
    try:
        prepare_magento2_import(OUTPUT_DIRECTORY, CHUNK_SIZE)
    except Exception as e:
        print(f"An error occurred: {e}")
