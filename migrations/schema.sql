-- ============================================================
-- SMARTQUALIHOME - MySQL Schema (single source of truth)
-- Fully consolidated: every column from prior migrations is now
-- included directly in its CREATE TABLE statement below.
-- Safe to run top-to-bottom on a brand-new empty database
-- (Aiven, PlanetScale, fresh XAMPP install, etc).
-- ============================================================

-- NOTE: CREATE DATABASE / USE intentionally omitted below.
-- Aiven (and most managed hosts) provide a fixed database name
-- (e.g. defaultdb) and free-tier accounts often lack permission
-- to CREATE DATABASE. Connect directly to your target database
-- (mysqlsh --schema=defaultdb ...) and this script will build all
-- tables inside it. For local XAMPP/MariaDB use, uncomment the two
-- lines below first.
-- CREATE DATABASE IF NOT EXISTS smartqualihome CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- USE smartqualihome;

-- Users
CREATE TABLE IF NOT EXISTS users (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    first_name     VARCHAR(80)  NOT NULL,
    middle_name    VARCHAR(80),
    last_name      VARCHAR(80)  NOT NULL,
    username       VARCHAR(60)  UNIQUE,
    contact_number VARCHAR(20),
    email          VARCHAR(120) NOT NULL UNIQUE,
    password_hash  VARCHAR(256) NOT NULL,
    role           ENUM('client','agent','admin') NOT NULL DEFAULT 'client',
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    is_verified    BOOLEAN NOT NULL DEFAULT TRUE,
    otp_code       VARCHAR(10) NULL,
    otp_expiry     DATETIME NULL,
    forgot_password_attempts INT NOT NULL DEFAULT 0,
    forgot_password_window_started_at DATETIME NULL,
    admin_dismissed_property_notifs TEXT,
    admin_dismissed_assessment_notifs TEXT,
    admin_dismissed_sale_notifs TEXT,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_users_email (email),
    INDEX idx_users_username (username),
    INDEX idx_users_role_active_created (role, is_active, created_at)
) ENGINE=InnoDB;

-- User profiles (clients, agents, admins)
CREATE TABLE IF NOT EXISTS user_profiles (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    user_id             INT NOT NULL UNIQUE,
    civil_status        ENUM('single','married','widowed','separated'),
    citizenship         ENUM('filipino','dual-citizen','foreign-national'),
    gender              ENUM('male','female','non-binary','prefer-not-to-say'),
    dependents          TINYINT UNSIGNED DEFAULT 0,
    birth_date          DATE,
    birthplace          VARCHAR(120),
    birth_region_code   VARCHAR(10),
    birth_region_name   VARCHAR(100),
    birth_province_code VARCHAR(10),
    birth_province_name VARCHAR(100),
    birth_citymun_code  VARCHAR(10),
    birth_citymun_name  VARCHAR(120),
    birth_barangay_code VARCHAR(15),
    birth_barangay_name VARCHAR(120),
    contact_number      VARCHAR(20),
    address             VARCHAR(255),
    employment_type     ENUM('employed','ofw-landbased','ofw-seafarer','licensed-professional','with-financial-support','with-attorney-in-fact','with-co-borrower'),
    employer_name       VARCHAR(120),
    employer_phone      VARCHAR(30),
    employer_email      VARCHAR(120),
    employer_business_address VARCHAR(255),
    employer_region_code VARCHAR(10),
    employer_region_name VARCHAR(100),
    employer_province_code VARCHAR(10),
    employer_province_name VARCHAR(100),
    employer_citymun_code VARCHAR(10),
    employer_citymun_name VARCHAR(120),
    employer_barangay_code VARCHAR(15),
    employer_barangay_name VARCHAR(120),
    sss_gsis_umid       VARCHAR(60),
    tin_no              VARCHAR(30),
    tenure_months       SMALLINT UNSIGNED,
    gross_income        DECIMAL(12,2),
    monthly_loans       DECIMAL(12,2) DEFAULT 0.00,
    other_deductions    DECIMAL(12,2) DEFAULT 0.00,
    age                 TINYINT UNSIGNED,
    avatar_data         LONGBLOB,
    avatar_mimetype     VARCHAR(50),
    banner_data         LONGBLOB,
    banner_mimetype     VARCHAR(50),
    has_valid_id        BOOLEAN,
    has_income_proof    BOOLEAN,
    valid_id_data       LONGBLOB,
    valid_id_mimetype   VARCHAR(80),
    valid_id_filename   VARCHAR(255),
    income_proof_data   LONGBLOB,
    income_proof_mimetype VARCHAR(80),
    income_proof_filename VARCHAR(255),
    esignature_data     LONGBLOB,
    esignature_mimetype VARCHAR(80),
    esignature_filename VARCHAR(255),
    preferred_type      ENUM('house-and-lot','condo','lot-only'),
    budget_min          DECIMAL(14,2) DEFAULT 0.00,
    budget_max          DECIMAL(14,2) DEFAULT 0.00,
    address_line        VARCHAR(255),
    home_region_code    VARCHAR(10),
    home_region_name    VARCHAR(100),
    home_province_code  VARCHAR(10),
    home_province_name  VARCHAR(100),
    home_citymun_code   VARCHAR(10),
    home_citymun_name   VARCHAR(120),
    home_barangay_code  VARCHAR(15),
    home_barangay_name  VARCHAR(120),
    street              VARCHAR(120),
    blk                 VARCHAR(30),
    lot                 VARCHAR(30),
    country             VARCHAR(80),
    zip_code            VARCHAR(20),
    subdivision_name    VARCHAR(120),
    social_instagram    VARCHAR(120),
    social_twitter_x    VARCHAR(120),
    social_viber        VARCHAR(40),
    social_whatsapp     VARCHAR(40),
    license_no          VARCHAR(60),
    contact_no          VARCHAR(20),
    bio                 TEXT,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Qualification results
CREATE TABLE IF NOT EXISTS qualification_results (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    user_id          INT NOT NULL,
    status           ENUM('Qualified','Conditionally Qualified','Not Qualified') NOT NULL,
    dti_ratio        FLOAT,
    max_loanable     DECIMAL(14,2),
    similarity_score FLOAT,
    assessment_mode  VARCHAR(20) DEFAULT 'reassess',
    factors_json     TEXT,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_qr_user (user_id),
    INDEX idx_qr_user_created (user_id, created_at),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Projects
CREATE TABLE IF NOT EXISTS projects (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(150) NOT NULL UNIQUE,
    street        VARCHAR(120),
    `block`       VARCHAR(30),
    lot_no        VARCHAR(30),
    location      VARCHAR(200),
    region_code   VARCHAR(10),
    region_name   VARCHAR(100),
    province_code VARCHAR(10),
    province_name VARCHAR(100),
    citymun_code  VARCHAR(10),
    citymun_name  VARCHAR(120),
    barangay_code VARCHAR(15),
    barangay_name VARCHAR(120),
    description   TEXT,
    images        TEXT COMMENT 'comma-separated image filenames',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_projects_name (name)
) ENGINE=InnoDB;

-- Subdivisions
CREATE TABLE IF NOT EXISTS subdivisions (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(150) NOT NULL UNIQUE,
    project_id    INT NULL,
    street        VARCHAR(120),
    `block`       VARCHAR(30),
    lot_no        VARCHAR(30),
    location      VARCHAR(200),
    region_code   VARCHAR(10),
    region_name   VARCHAR(100),
    province_code VARCHAR(10),
    province_name VARCHAR(100),
    citymun_code  VARCHAR(10),
    citymun_name  VARCHAR(120),
    barangay_code VARCHAR(15),
    barangay_name VARCHAR(120),
    description   TEXT,
    images        TEXT COMMENT 'comma-separated image filenames',
    image_data    LONGBLOB,
    image_mimetype VARCHAR(50),
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_subdivisions_project_id (project_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Properties
CREATE TABLE IF NOT EXISTS properties (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(120) NOT NULL,
    street          VARCHAR(120),
    `block`         VARCHAR(30),
    lot_no          VARCHAR(30),
    location        VARCHAR(150) NOT NULL,
    region          VARCHAR(100),
    region_code     VARCHAR(10),
    region_name     VARCHAR(100),
    province_code   VARCHAR(10),
    province_name   VARCHAR(100),
    citymun_code    VARCHAR(10),
    citymun_name    VARCHAR(120),
    barangay_code   VARCHAR(15),
    barangay_name   VARCHAR(120),
    prop_type       VARCHAR(40),
    unit_type       VARCHAR(40),
    price           DECIMAL(14,2) NOT NULL,
    promo_discount_rate DECIMAL(5,2),
    reservation_fee DECIMAL(14,2),
    downpayment_rate DECIMAL(5,2),
    downpayment_terms_months INT,
    loanable_percentage DECIMAL(5,2),
    vat_rate        DECIMAL(5,2),
    lmf_rate        DECIMAL(5,2),
    annual_interest_rate DECIMAL(5,2),
    bedrooms        TINYINT UNSIGNED,
    bathrooms       TINYINT UNSIGNED,
    storeys         TINYINT UNSIGNED,
    floor_area      FLOAT COMMENT 'sqm',
    lot_area        FLOAT COMMENT 'sqm',
    description     TEXT,
    images          TEXT         COMMENT 'comma-separated filenames',
    agent_id        INT,
    subdivision_id  INT NULL,
    unit_id         VARCHAR(60) NULL,
    status          ENUM('available','sold','reserved') DEFAULT 'available',
    approval_status VARCHAR(20) DEFAULT 'approved' COMMENT 'pending / approved / rejected',
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (agent_id)       REFERENCES users(id)        ON DELETE SET NULL,
    FOREIGN KEY (subdivision_id) REFERENCES subdivisions(id) ON DELETE SET NULL,
    INDEX idx_properties_unit_id (unit_id),
    INDEX idx_properties_agent_id (agent_id),
    INDEX idx_properties_subdivision_id (subdivision_id),
    INDEX idx_properties_status_approval_created (status, approval_status, created_at)
) ENGINE=InnoDB;

-- Tripping requests
CREATE TABLE IF NOT EXISTS tripping_requests (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    client_id      INT NOT NULL,
    property_id    INT NOT NULL,
    preferred_date DATE NOT NULL,
    preferred_time VARCHAR(10),
    status         ENUM('pending','approved','visited','rejected','sold') DEFAULT 'pending',
    agent_note     TEXT,
    notification_read BOOLEAN NOT NULL DEFAULT FALSE,
    purchase_form_submitted BOOLEAN NOT NULL DEFAULT FALSE,
    purchase_form_submitted_at DATETIME NULL,
    purchase_form_data LONGTEXT NULL,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_trip_client (client_id),
    INDEX idx_trip_property_id (property_id),
    INDEX idx_trip_client_created (client_id, created_at),
    INDEX idx_trip_property_status_read (property_id, status, notification_read),
    FOREIGN KEY (client_id)   REFERENCES users(id)       ON DELETE CASCADE,
    FOREIGN KEY (property_id) REFERENCES properties(id)  ON DELETE CASCADE
) ENGINE=InnoDB;

-- Agent availability slots
CREATE TABLE IF NOT EXISTS agent_availability (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    agent_id       INT NOT NULL,
    available_date DATE NOT NULL,
    availability_status VARCHAR(20) NOT NULL DEFAULT 'available',
    start_time     TIME NOT NULL,
    end_time       TIME NOT NULL,
    notes          VARCHAR(255),
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_aa_agent_date (agent_id, available_date),
    INDEX idx_aa_date (available_date),
    FOREIGN KEY (agent_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Property sales (closed deals / bought properties)
CREATE TABLE IF NOT EXISTS property_sales (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    property_id   INT NOT NULL UNIQUE,
    client_id     INT NOT NULL,
    trip_id       INT UNIQUE,
    agent_id      INT,
    selling_price DECIMAL(14,2),
    note          TEXT,
    sold_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ps_client   (client_id),
    INDEX idx_ps_agent    (agent_id),
    INDEX idx_ps_agent_sold (agent_id, sold_at),
    INDEX idx_ps_client_sold (client_id, sold_at),
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE,
    FOREIGN KEY (client_id)   REFERENCES users(id)      ON DELETE CASCADE,
    FOREIGN KEY (trip_id)     REFERENCES tripping_requests(id) ON DELETE SET NULL,
    FOREIGN KEY (agent_id)    REFERENCES users(id)      ON DELETE SET NULL
) ENGINE=InnoDB;

-- Training data (ML)
-- notes may store AUTO_SYNC_SALE_ID=<sale_id> markers for app-level dedupe
-- when syncing from historical_buyer_records into training_data.
CREATE TABLE IF NOT EXISTS training_data (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    civil_status     VARCHAR(20),
    dependents       TINYINT,
    age              INT DEFAULT 30,
    employment_type  VARCHAR(30),
    tenure_months    INT,
    gross_income     DECIMAL(12,2),
    monthly_loans    DECIMAL(12,2),
    other_deductions DECIMAL(12,2),
    dti_ratio        FLOAT,
    outcome          ENUM('Qualified','Conditionally Qualified','Not Qualified') NOT NULL,
    notes            VARCHAR(255),
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_td_notes (notes(64))
) ENGINE=InnoDB;

-- Historical buyer records (actual sold transactions)
CREATE TABLE IF NOT EXISTS historical_buyer_records (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    sale_id          INT NOT NULL UNIQUE,
    client_id        INT NOT NULL,
    property_id      INT,
    civil_status     VARCHAR(20),
    dependents       INT DEFAULT 0,
    age              INT DEFAULT 30,
    employment_type  VARCHAR(30),
    tenure_months    INT DEFAULT 0,
    gross_income     DECIMAL(12,2),
    monthly_loans    DECIMAL(12,2),
    dti_ratio        FLOAT,
    outcome          VARCHAR(30),
    notes            VARCHAR(255),
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_hbr_client   (client_id),
    INDEX idx_hbr_property (property_id),
    FOREIGN KEY (sale_id)     REFERENCES property_sales(id) ON DELETE CASCADE,
    FOREIGN KEY (client_id)   REFERENCES users(id)          ON DELETE CASCADE,
    FOREIGN KEY (property_id) REFERENCES properties(id)     ON DELETE SET NULL
) ENGINE=InnoDB;

-- Activity logs
CREATE TABLE IF NOT EXISTS activity_logs (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    actor_id    INT NULL,
    actor_name  VARCHAR(160) NOT NULL DEFAULT 'System',
    actor_role  VARCHAR(20)  NOT NULL DEFAULT 'system',
    action      VARCHAR(40)  NOT NULL,
    description VARCHAR(500) NOT NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_al_actor   (actor_id),
    INDEX idx_al_action  (action),
    INDEX idx_al_created (created_at),
    FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB;
-- Common action values include:
-- login, logout, register, assessment, prop_approve, prop_reject,
-- sale_marked, sub_create, sub_edit, sub_delete,
-- user_toggle, full_details_request, full_details_approved, full_details_rejected.
-- c50_add_record, c50_edit_record, c50_del_record,
-- c50_retrain, c50_seed, c50_sync_historical, c50_retrain_async.

-- System configuration (qualification criteria and settings)
CREATE TABLE IF NOT EXISTS system_config (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    `key`       VARCHAR(64)  NOT NULL UNIQUE,
    value       VARCHAR(255) NOT NULL,
    label       VARCHAR(128),
    description VARCHAR(512),
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_sc_key (`key`)
) ENGINE=InnoDB;

-- Agent notifications (listing updates from admin actions)
CREATE TABLE IF NOT EXISTS agent_notifications (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    agent_id    INT NOT NULL,
    property_id INT NULL,
    event_type  VARCHAR(40) NOT NULL,
    message     VARCHAR(255) NOT NULL,
    is_read     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_an_agent   (agent_id),
    INDEX idx_an_created (created_at),
    INDEX idx_an_property_id (property_id),
    INDEX idx_an_agent_type_read_created (agent_id, event_type, is_read, created_at),
    FOREIGN KEY (agent_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Client requests to view complete pricing details (agent approval flow)
-- One request row is kept per client/property pair and transitions through
-- pending -> approved/rejected while preserving review metadata.
CREATE TABLE IF NOT EXISTS property_pricing_detail_requests (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    client_id            INT NOT NULL,
    property_id          INT NOT NULL,
    status               ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
    agent_note           TEXT,
    reviewed_by_agent_id INT NULL,
    reviewed_at          DATETIME NULL,
    created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_pricing_detail_client_property (client_id, property_id),
    INDEX idx_pdr_client (client_id),
    INDEX idx_pdr_property (property_id),
    INDEX idx_ppdr_reviewed_by (reviewed_by_agent_id),
    INDEX idx_ppdr_status (status),
    FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE,
    FOREIGN KEY (reviewed_by_agent_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- History trail for pricing detail requests
CREATE TABLE IF NOT EXISTS property_pricing_detail_request_history (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    request_id           INT NULL,
    client_id            INT NOT NULL,
    property_id          INT NOT NULL,
    status               ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
    agent_note           TEXT,
    reviewed_by_agent_id INT NULL,
    requested_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at          DATETIME NULL,
    created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_pdrh_request (request_id),
    INDEX idx_pdrh_client (client_id),
    INDEX idx_pdrh_property (property_id),
    INDEX idx_pdrh_reviewed_by (reviewed_by_agent_id),
    INDEX idx_pdrh_request_status (request_id, status),
    INDEX idx_pdrh_property_requested (property_id, requested_at),
    FOREIGN KEY (request_id) REFERENCES property_pricing_detail_requests(id) ON DELETE SET NULL,
    FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (property_id) REFERENCES properties(id) ON DELETE CASCADE,
    FOREIGN KEY (reviewed_by_agent_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB;