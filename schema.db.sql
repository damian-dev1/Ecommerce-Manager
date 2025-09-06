BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS attribute_groups (
    group_id    INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS attribute_options (
    option_id       INTEGER PRIMARY KEY,
    attribute_id    INTEGER NOT NULL REFERENCES attributes(attribute_id) ON UPDATE CASCADE ON DELETE CASCADE,
    value           TEXT NOT NULL,         -- canonical stored value
    label           TEXT,                  -- display label
    sort_order      INTEGER DEFAULT 0,
    UNIQUE(attribute_id, value)
);
CREATE TABLE IF NOT EXISTS attributes (
    attribute_id    INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,   -- e.g., "color", "size", "material"
    label           TEXT NOT NULL,          -- display name
    data_type       TEXT NOT NULL CHECK (data_type IN ('text','int','decimal','bool','date','enum','json')),
    unit_code       TEXT,                   -- optional unit hint (e.g., "cm","kg")
    is_variant      INTEGER NOT NULL DEFAULT 0 CHECK (is_variant IN (0,1)),  -- used for variant differentiation
    is_required     INTEGER NOT NULL DEFAULT 0 CHECK (is_required IN (0,1)),
    is_facet        INTEGER NOT NULL DEFAULT 1 CHECK (is_facet IN (0,1)),
    group_id        INTEGER REFERENCES attribute_groups(group_id) ON UPDATE CASCADE ON DELETE SET NULL,
    sort_order      INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS brands (
    brand_id        INTEGER PRIMARY KEY,
    brand_name      TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS categories (
    category_id     INTEGER PRIMARY KEY,
    code            TEXT UNIQUE,                     -- your internal code/slug if any
    name            TEXT NOT NULL,                   -- human-readable name
    parent_id       INTEGER REFERENCES categories(category_id) ON UPDATE CASCADE ON DELETE SET NULL,
    gcc_code        TEXT,                            -- Google product category code (optional)
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS category_attributes (
    category_id     INTEGER NOT NULL REFERENCES categories(category_id) ON UPDATE CASCADE ON DELETE CASCADE,
    attribute_id    INTEGER NOT NULL REFERENCES attributes(attribute_id)  ON UPDATE CASCADE ON DELETE CASCADE,
    is_required     INTEGER NOT NULL DEFAULT 0 CHECK (is_required IN (0,1)),
    sort_order      INTEGER DEFAULT 0,
    PRIMARY KEY (category_id, attribute_id)
);
CREATE TABLE IF NOT EXISTS dimensions (
    dimension_id                INTEGER PRIMARY KEY,
    product_height_cm           REAL NOT NULL CHECK (product_height_cm >= 0),
    product_width_cm            REAL NOT NULL CHECK (product_width_cm  >= 0),
    product_depth_cm            REAL NOT NULL CHECK (product_depth_cm  >= 0),
    package_height_cm           REAL CHECK (package_height_cm  >= 0),
    package_width_cm            REAL CHECK (package_width_cm   >= 0),
    package_depth_cm            REAL CHECK (package_depth_cm   >= 0),
    package_gross_weight_kg     REAL CHECK (package_gross_weight_kg >= 0)
);
CREATE TABLE IF NOT EXISTS invoice_line_items (
    line_item_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id          INTEGER NOT NULL REFERENCES invoices(invoice_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_sku         TEXT NOT NULL,
    description         TEXT,
    quantity_billed     INTEGER NOT NULL CHECK (quantity_billed >= 0),
    unit_price          NUMERIC NOT NULL CHECK (unit_price >= 0),
    currency_code       CHAR(3),
    total_price         NUMERIC GENERATED ALWAYS AS (quantity_billed * unit_price) STORED
);
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id                   INTEGER NOT NULL REFERENCES vendors(vendor_id)      ON UPDATE CASCADE ON DELETE RESTRICT,
    po_id                       INTEGER REFERENCES purchase_orders(po_id)           ON UPDATE CASCADE ON DELETE SET NULL,
    invoice_number              TEXT UNIQUE NOT NULL,
    invoice_date                DATE NOT NULL,
    due_date                    DATE,
    currency_code               CHAR(3),
    subtotal_amount             NUMERIC NOT NULL,
    tax_amount                  NUMERIC,
    vat_number                  TEXT,
    total_amount                NUMERIC NOT NULL,
    payment_status              TEXT DEFAULT 'Unpaid',
    payment_method              TEXT,
    remittance_reference        TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pack_contents (
    pack_content_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    part_number         TEXT NOT NULL REFERENCES products(part_number) ON UPDATE CASCADE ON DELETE CASCADE,
    item_description    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    payment_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id          INTEGER NOT NULL REFERENCES invoices(invoice_id) ON UPDATE CASCADE ON DELETE CASCADE,
    payment_date        DATE NOT NULL,
    amount_paid         NUMERIC NOT NULL CHECK (amount_paid >= 0),
    currency_code       CHAR(3),
    payment_method      TEXT,
    transaction_reference TEXT,
    payer_account       TEXT,
    payment_status      TEXT DEFAULT 'Completed'
);
CREATE TABLE IF NOT EXISTS po_line_items (
    line_item_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id               INTEGER NOT NULL REFERENCES purchase_orders(po_id) ON UPDATE CASCADE ON DELETE CASCADE,
    product_sku         TEXT NOT NULL,      -- keep free-form; link externally if needed
    description         TEXT,
    quantity_ordered    INTEGER NOT NULL CHECK (quantity_ordered >= 0),
    unit_price          NUMERIC NOT NULL CHECK (unit_price >= 0),
    currency_code       CHAR(3),
    total_price         NUMERIC GENERATED ALWAYS AS (quantity_ordered * unit_price) STORED
);
CREATE TABLE IF NOT EXISTS prices (
    price_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    part_number         TEXT NOT NULL REFERENCES products(part_number) ON UPDATE CASCADE ON DELETE CASCADE,
    currency_code       CHAR(3) DEFAULT 'AUD',
    msrp                NUMERIC,
    rrp                 NUMERIC,
    retail_price        NUMERIC NOT NULL CHECK (retail_price >= 0),
    discount_price      NUMERIC,
    cost_price_ex_tax   NUMERIC,
    effective_date      DATE NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (part_number, effective_date)
);
CREATE TABLE IF NOT EXISTS product_attribute_values (
    part_number     TEXT NOT NULL REFERENCES products(part_number) ON UPDATE CASCADE ON DELETE CASCADE,
    attribute_id    INTEGER NOT NULL REFERENCES attributes(attribute_id) ON UPDATE CASCADE ON DELETE CASCADE,

    value_text      TEXT,
    value_int       INTEGER,
    value_decimal   NUMERIC,
    value_bool      INTEGER CHECK (value_bool IN (0,1)),
    value_date      DATE,
    value_json      TEXT,
    option_id       INTEGER REFERENCES attribute_options(option_id) ON UPDATE CASCADE ON DELETE SET NULL,

    unit_code       TEXT,                           -- override/hint if needed per value
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (part_number, attribute_id),

    -- enforce exactly one value column populated
    CHECK (
        (value_text    IS NOT NULL) +
        (value_int     IS NOT NULL) +
        (value_decimal IS NOT NULL) +
        (value_bool    IS NOT NULL) +
        (value_date    IS NOT NULL) +
        (value_json    IS NOT NULL) +
        (option_id     IS NOT NULL)
        = 1
    )
);
CREATE TABLE IF NOT EXISTS product_categories (
    part_number   TEXT NOT NULL REFERENCES products(part_number) ON UPDATE CASCADE ON DELETE CASCADE,
    category_id   INTEGER NOT NULL REFERENCES categories(category_id) ON UPDATE CASCADE ON DELETE CASCADE,
    PRIMARY KEY (part_number, category_id)
);
CREATE TABLE IF NOT EXISTS product_media (
    media_id        INTEGER PRIMARY KEY,
    part_number     TEXT NOT NULL REFERENCES products(part_number) ON UPDATE CASCADE ON DELETE CASCADE,
    media_type      TEXT NOT NULL CHECK (media_type IN ('image','video','youtube')),  -- facet
    url             TEXT NOT NULL,
    alt_text        TEXT,
    position        INTEGER DEFAULT 0,                           -- ordering
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_variants (
    variant_part_number   TEXT PRIMARY KEY REFERENCES products(part_number) ON UPDATE CASCADE ON DELETE CASCADE,
    parent_part_number    TEXT NOT NULL REFERENCES products(part_number)   ON UPDATE CASCADE ON DELETE CASCADE,
    UNIQUE(variant_part_number, parent_part_number)
);
CREATE TABLE IF NOT EXISTS products (
    part_number                 TEXT PRIMARY KEY,                -- keep as your business PK
    sap_article_id              INTEGER UNIQUE,
    barcode                     TEXT UNIQUE,
    model_code                  TEXT,
    brand_id                    INTEGER NOT NULL REFERENCES brands(brand_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    other_brand_name            TEXT,

    short_description           TEXT,
    secondary_short_description TEXT,
    full_description            TEXT,

    main_colour_name            TEXT,        -- -> attribute "color"
    suitable_age_range          TEXT,        -- -> attribute "age_range"
    sports_size_code            TEXT,        -- -> attribute "size"

    country_of_origin_code      CHAR(2),
    supplier_comments           TEXT,

    primary_category_id         INTEGER REFERENCES categories(category_id) ON UPDATE CASCADE ON DELETE SET NULL,

    warranty_id                 INTEGER,
    dimension_id                INTEGER,
    vendor_id                   INTEGER,

    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (warranty_id)   REFERENCES warranty(warranty_id)   ON UPDATE CASCADE ON DELETE SET NULL,
    FOREIGN KEY (dimension_id)  REFERENCES dimensions(dimension_id) ON UPDATE CASCADE ON DELETE SET NULL,
    FOREIGN KEY (vendor_id)     REFERENCES vendors(vendor_id)      ON UPDATE CASCADE ON DELETE SET NULL,

    CHECK (length(part_number) > 0)
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id                   INTEGER NOT NULL REFERENCES vendors(vendor_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    po_number                   TEXT UNIQUE NOT NULL,
    order_date                  DATE NOT NULL,
    expected_delivery_date      DATE,
    incoterms                   TEXT,
    currency_code               CHAR(3),
    total_amount                NUMERIC NOT NULL,
    payment_terms               TEXT,
    shipping_method             TEXT,
    shipping_address            TEXT,
    billing_address             TEXT,
    status                      TEXT DEFAULT 'Pending',
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS records (
    record_id       INTEGER PRIMARY KEY,
    file_name       TEXT NOT NULL,
    row_data        TEXT NOT NULL,
    processed_at    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS shipment_tracking (
    tracking_event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id         INTEGER NOT NULL REFERENCES shipments(shipment_id) ON UPDATE CASCADE ON DELETE CASCADE,
    event_timestamp     TIMESTAMP NOT NULL,
    location            TEXT,
    status_update       TEXT
);
CREATE TABLE IF NOT EXISTS shipments (
    shipment_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id               INTEGER NOT NULL REFERENCES purchase_orders(po_id) ON UPDATE CASCADE ON DELETE CASCADE,
    shipment_date       DATE NOT NULL,
    carrier_name        TEXT,
    tracking_number     TEXT,
    incoterms           TEXT,
    shipment_status     TEXT DEFAULT 'In Transit',
    estimated_arrival   DATE,
    actual_arrival      DATE
);
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id               INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identification & Classification
    legal_entity_name       TEXT NOT NULL,
    trading_name            TEXT,
    account_reference       TEXT,
    sap_supplier_id         TEXT,
    vendor_status           TEXT,
    product_category        TEXT,

    -- Contact
    contact_person_name     TEXT,
    contact_email           TEXT,
    contact_phone           TEXT,
    website_url             TEXT,

    -- Address
    street_address          TEXT,
    postal_code             TEXT,
    city                    TEXT,
    state_province_region   TEXT,
    country_code            CHAR(2),

    -- Legal & Tax
    abn                     TEXT,
    acn                     TEXT,
    vat_number              TEXT,
    eori_number             TEXT,
    tax_residency_country   CHAR(2),

    -- Commercial Terms
    payment_terms           TEXT,
    incoterms               TEXT,
    freight_matrix          TEXT,
    currency_code           CHAR(3),

    -- Integration
    platform_name           TEXT,
    api_integration_status  TEXT,

    -- Governance & Source
    vendor_manager_name     TEXT,
    onboarding_source       TEXT,

    -- Timestamps
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS warranty (
    warranty_id                 INTEGER PRIMARY KEY,
    duration_months             INTEGER NOT NULL CHECK (duration_months >= 0),
    warranty_type_code          TEXT NOT NULL
);
CREATE VIEW vw_product_catalog AS
SELECT
    p.part_number,
    p.model_code,
    p.barcode,
    p.sap_article_id,
    p.short_description,
    p.secondary_short_description,
    p.full_description,
    p.country_of_origin_code,
    p.supplier_comments,

    b.brand_name,

    c.code            AS category_code,
    c.name            AS category_name,
    c.gcc_code        AS category_gcc_code,

    v.legal_entity_name AS vendor_name,
    v.country_code      AS vendor_country,

    w.warranty_type_code,
    w.duration_months,

    d.product_height_cm,
    d.product_width_cm,
    d.product_depth_cm,
    d.package_height_cm,
    d.package_width_cm,
    d.package_depth_cm,
    d.package_gross_weight_kg,

    -- first image by position
    (
        SELECT pm.url
        FROM product_media pm
        WHERE pm.part_number = p.part_number AND pm.media_type = 'image'
        ORDER BY pm.position ASC
        LIMIT 1
    ) AS image_main_url,

    -- first youtube link if any
    (
        SELECT pm.url
        FROM product_media pm
        WHERE pm.part_number = p.part_number AND pm.media_type = 'youtube'
        ORDER BY pm.position ASC
        LIMIT 1
    ) AS youtube_url,

    pr.currency_code,
    pr.msrp,
    pr.rrp,
    pr.retail_price,
    pr.discount_price,
    pr.cost_price_ex_tax,
    pr.effective_date,

    p.created_at  AS product_created_at,
    p.updated_at  AS product_updated_at

FROM products p
LEFT JOIN brands     b ON p.brand_id = b.brand_id
LEFT JOIN categories c ON p.primary_category_id = c.category_id
LEFT JOIN vendors    v ON p.vendor_id = v.vendor_id
LEFT JOIN warranty   w ON p.warranty_id = w.warranty_id
LEFT JOIN dimensions d ON p.dimension_id = d.dimension_id
LEFT JOIN (
    SELECT part_number, MAX(effective_date) AS latest_date
    FROM prices
    GROUP BY part_number
) latest_price ON p.part_number = latest_price.part_number
LEFT JOIN prices pr
    ON p.part_number = pr.part_number
   AND pr.effective_date = latest_price.latest_date;
CREATE INDEX idx_attr_category ON category_attributes(category_id, attribute_id);
CREATE INDEX idx_attr_code ON attributes(code);
CREATE INDEX idx_categories_parent ON categories(parent_id);
CREATE INDEX idx_media_lookup ON product_media(part_number, media_type, position);
CREATE INDEX idx_media_product ON product_media(part_number);
CREATE INDEX idx_media_type_pos ON product_media(media_type, position);
CREATE INDEX idx_pav_attr_bool    ON product_attribute_values(attribute_id, value_bool);
CREATE INDEX idx_pav_attr_date    ON product_attribute_values(attribute_id, value_date);
CREATE INDEX idx_pav_attr_decimal ON product_attribute_values(attribute_id, value_decimal);
CREATE INDEX idx_pav_attr_int     ON product_attribute_values(attribute_id, value_int);
CREATE INDEX idx_pav_attr_option  ON product_attribute_values(attribute_id, option_id);
CREATE INDEX idx_pav_attr_text    ON product_attribute_values(attribute_id, value_text);
CREATE INDEX idx_pav_product      ON product_attribute_values(part_number);
CREATE INDEX idx_price_lookup ON prices(part_number, currency_code, effective_date);
CREATE INDEX idx_prices_product_date ON prices(part_number, effective_date DESC);
CREATE INDEX idx_product_categories_cat ON product_categories(category_id);
CREATE INDEX idx_products_barcode ON products(barcode);
CREATE INDEX idx_products_brand ON products(brand_id);
CREATE INDEX idx_products_model_code ON products(model_code);
CREATE INDEX idx_products_primary_category ON products(primary_category_id);
CREATE INDEX idx_products_sap_article_id ON products(sap_article_id);
CREATE INDEX idx_variant_parent ON product_variants(parent_part_number);
CREATE TRIGGER trg_attributes_updated_at
AFTER UPDATE ON attributes
FOR EACH ROW
BEGIN
    UPDATE attributes SET updated_at = CURRENT_TIMESTAMP WHERE attribute_id = NEW.attribute_id;
END;
CREATE TRIGGER trg_categories_updated_at
AFTER UPDATE ON categories
FOR EACH ROW
BEGIN
    UPDATE categories SET updated_at = CURRENT_TIMESTAMP WHERE category_id = NEW.category_id;
END;
CREATE TRIGGER trg_media_updated_at
AFTER UPDATE ON product_media
FOR EACH ROW
BEGIN
    UPDATE product_media
    SET updated_at = CURRENT_TIMESTAMP
    WHERE media_id = NEW.media_id;
END;
CREATE TRIGGER trg_pav_updated_at
AFTER UPDATE ON product_attribute_values
FOR EACH ROW
BEGIN
    UPDATE product_attribute_values
    SET updated_at = CURRENT_TIMESTAMP
    WHERE part_number = NEW.part_number AND attribute_id = NEW.attribute_id;
END;
CREATE TRIGGER trg_prices_updated_at
AFTER UPDATE ON prices
FOR EACH ROW
BEGIN
    UPDATE prices SET updated_at = CURRENT_TIMESTAMP WHERE price_id = NEW.price_id;
END;
CREATE TRIGGER trg_products_updated_at
AFTER UPDATE ON products
FOR EACH ROW
BEGIN
    UPDATE products SET updated_at = CURRENT_TIMESTAMP WHERE part_number = NEW.part_number;
END;
CREATE TRIGGER trg_vendors_updated_at
AFTER UPDATE ON vendors
FOR EACH ROW
BEGIN
    UPDATE vendors SET updated_at = CURRENT_TIMESTAMP WHERE vendor_id = NEW.vendor_id;
END;
COMMIT;
