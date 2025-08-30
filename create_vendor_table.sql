CREATE TABLE IF NOT EXISTS vendors (
    vendor_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Identification & Classification
    legal_entity_name       TEXT NOT NULL,                     -- vendor name
    trading_name            TEXT,                              -- optional alias or brand name
    account_reference       TEXT,
    sap_supplier_id         TEXT,                              -- SAP integration ID
    vendor_status           TEXT,
    product_category        TEXT,                              -- goods/services classification

    -- Contact Information
    contact_person_name     TEXT,
    contact_email           TEXT,
    contact_phone           TEXT,
    website_url             TEXT,

    -- Address & Location
    street_address          TEXT,                              
    postal_code             TEXT,
    city                    TEXT,
    state_province_region   TEXT,
    country_code            CHAR(2),                           -- ISO 3166-1 alpha-2

    -- Legal & Tax Identifiers
    abn                     TEXT,                              -- Australian Business Number
    acn                     TEXT,                              -- Australian Company Number
    vat_number              TEXT,                              -- Value Added Tax ID (EU, UK, etc.)
    eori_number             TEXT,                              -- Economic Operators Registration and Identification (EU)
    tax_residency_country   CHAR(2),                           -- for cross-border compliance

    -- Commercial Terms
    payment_terms           TEXT,
    incoterms               TEXT,                              -- e.g., FOB, CIF, DDP
    freight_matrix          TEXT,
    currency_code           CHAR(3),                           -- ISO 4217 (e.g., AUD, USD)

    -- Integration & Platform
    platform_name           TEXT,                              
    api_integration_status  TEXT,                              

    -- Governance & Source
    vendor_manager_name     TEXT,
    onboarding_source       TEXT,

    -- Timestamps
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
