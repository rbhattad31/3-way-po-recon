-- ============================================================
-- Azure SQL Database -- Minimal test schema + seed data
-- for VoucherSQLServerERPConnector
--
-- Run this on a fresh Azure SQL Database named SRPL2025.
-- Only creates the 7 tables read by the connector queries.
-- All other tables from script.sql are NOT needed for testing.
-- ============================================================

-- ============================================================
-- 1. VENDOR MASTER
-- ============================================================
IF OBJECT_ID('dbo.Master_Table', 'U') IS NULL
CREATE TABLE [dbo].[Master_Table] (
    [VoucherSeries]  nvarchar(125) NULL,
    [VoucherNo]      int           NULL,
    [VoucherLineNo]  int           NULL DEFAULT (1),
    [MasterName]     nvarchar(125) NOT NULL,
    [MasterType]     nvarchar(125) NOT NULL,
    [ID]             uniqueidentifier NOT NULL CONSTRAINT [DF_Master_Table_ID] DEFAULT (newid()),
    [inactive]       nvarchar(10)  NULL DEFAULT ('False'),
    CONSTRAINT [PK_Master_Table_1] PRIMARY KEY CLUSTERED (
        [MasterName] ASC,
        [MasterType] ASC,
        [ID]         ASC
    )
);

IF OBJECT_ID('dbo.Master_MasterCodes_Table', 'U') IS NULL
CREATE TABLE [dbo].[Master_MasterCodes_Table] (
    [VoucherLineNo]  int           NOT NULL DEFAULT (1),
    [VoucherSeries]  nvarchar(125) NULL,
    [VoucherNo]      int           NULL,
    [MasterName]     nvarchar(125) NOT NULL,
    [MasterCode]     nvarchar(125) NOT NULL,
    [mastertype]     nvarchar(125) NULL,
    CONSTRAINT [PK_Master_MasterCodes_Table] PRIMARY KEY CLUSTERED (
        [MasterName] ASC,
        [MasterCode] ASC
    )
);

IF OBJECT_ID('dbo.Master_RegistrationDetails_Table', 'U') IS NULL
CREATE TABLE [dbo].[Master_RegistrationDetails_Table] (
    [VoucherLineNo]       int           NOT NULL DEFAULT (1),
    [VoucherSeries]       nvarchar(125) NULL,
    [VoucherNo]           int           NULL,
    [MasterName]          nvarchar(125) NULL,
    [PANo]                nvarchar(50)  NULL,
    [TIN]                 nvarchar(50)  NULL,
    [GST]                 nvarchar(50)  NULL,
    [GSTIN]               nvarchar(100) NULL,
    [MSMEType]            nvarchar(150) NULL,
    [MSMERegStatus]       nvarchar(10)  NULL,
    [MSMENo]              nvarchar(150) NULL
);

-- ============================================================
-- 2. PURCHASE ORDERS (stored as vouchers in Transaction tables)
-- ============================================================
IF OBJECT_ID('dbo.Transaction_Header_Table', 'U') IS NULL
CREATE TABLE [dbo].[Transaction_Header_Table] (
    [VoucherLineNo]       int            NOT NULL DEFAULT (1),
    [VoucherSeries]       nvarchar(50)   NULL,
    [VoucherNo]           int            NULL,
    [Date]                datetime       NULL,
    [Branch]              nvarchar(50)   NULL,
    [Location]            nvarchar(50)   NULL,
    [Account]             nvarchar(125)  NULL,
    [ContraAccount]       nvarchar(125)  NULL,
    [Remarks]             nvarchar(250)  NULL,
    [PartyRefDoc]         nvarchar(50)   NULL,
    [PartyRefDocDate]     datetime       NULL DEFAULT (getdate()),
    [ReferenceVoucherSeries] nvarchar(125) NULL,
    [ReferenceVoucherNo]  nvarchar(50)   NULL,
    [TotalBillValue]      decimal(18, 6) NULL,
    [TotalNet]            decimal(18, 6) NULL,
    [Currency]            nvarchar(50)   NULL,
    [ExchangeRate]        decimal(18, 6) NULL,
    [TransactionName]     nvarchar(125)  NULL,
    [GSTIN]               nvarchar(25)   NULL,
    [IRNNo]               nvarchar(150)  NULL,
    CONSTRAINT [CHK_Vsrs] CHECK (ISNULL([VoucherSeries], '') <> '')
);

IF OBJECT_ID('dbo.Transaction_ItemBody_Table', 'U') IS NULL
CREATE TABLE [dbo].[Transaction_ItemBody_Table] (
    [VoucherLineNo]   int            NOT NULL DEFAULT (1),
    [VoucherSeries]   nvarchar(50)   NULL,
    [VoucherNo]       int            NULL,
    [Code]            nvarchar(50)   NULL,
    [Product]         nvarchar(125)  NULL,
    [Unit]            nvarchar(50)   NULL,
    [Quantity]        decimal(18, 5) NULL,
    [Rate]            decimal(18, 6) NULL,
    [Gross]           decimal(18, 6) NULL,
    [Discount]        decimal(18, 6) NULL,
    [Net]             decimal(18, 6) NULL,
    [Description]     nvarchar(125)  NULL,
    [CostCentre]      nvarchar(50)   NULL,
    [Department]      nvarchar(50)   NULL,
    [RID]             nvarchar(50)   NULL
);

-- ============================================================
-- 3. GOODS RECEIPT NOTES (separate EFI table, not voucher table)
-- ============================================================
IF OBJECT_ID('dbo.EFIMRDetailsTable', 'U') IS NULL
CREATE TABLE [dbo].[EFIMRDetailsTable] (
    [CompNum]       int            NULL,
    [PlantCode]     varchar(16)    NOT NULL,
    [GRNDATE]       date           NULL,
    [GRNNO]         varchar(60)    NULL,
    [POrderNum]     int            NOT NULL,
    [POrderLineNum] int            NOT NULL,
    [CURRENCYCODE]  varchar(8)     NULL,
    [ItemCode]      varchar(50)    NULL,
    [GROUPCODE]     varchar(16)    NULL,
    [ItemDesc]      varchar(2000)  NULL,
    [ORDERQTY]      numeric(28, 3) NULL,
    [GRNQTY]        numeric(29, 3) NULL,
    [UNITPRICE]     numeric(38, 6) NULL,
    [AMOUNT]        numeric(38, 6) NULL,
    [PriceUnitCode] varchar(20)    NULL,
    [Suppcode]      varchar(16)    NULL,
    [SuppName]      varchar(70)    NULL,
    [WhouseCode]    varchar(16)    NOT NULL,
    [Quarantined]   bit            NULL,
    [CURRATE]       numeric(30, 8) NOT NULL DEFAULT (1),
    [GRNPRICE]      numeric(30, 6) NULL,
    [GRNVALUE]      numeric(38, 6) NULL,
    [POrderDate]    date           NULL
);

-- ============================================================
-- 4. INVOICES / PAYMENTS (duplicate-check and invoice reference)
-- ============================================================
IF OBJECT_ID('dbo.Transaction_Payments_Table', 'U') IS NULL
CREATE TABLE [dbo].[Transaction_Payments_Table] (
    [VoucherLineNo]     int            NOT NULL DEFAULT (1),
    [VoucherSeries]     nvarchar(50)   NULL,
    [VoucherNo]         int            NULL,
    [PaymentMethod]     nvarchar(10)   NULL,
    [PaymentAccount]    nvarchar(150)  NULL,
    [PartyAccount]      nvarchar(150)  NULL,
    [Amount]            decimal(18, 6) NULL,
    [NetAmount]         decimal(18, 6) NULL,
    [InstrumentType]    nvarchar(50)   NULL,
    [InstrumentNo]      nvarchar(50)   NULL,
    [InstrumentDate]    datetime       NULL,
    [TVoucherSeries]    nvarchar(50)   NULL,
    [TVoucherNo]        int            NULL,
    [TowardsVNo]        nvarchar(50)   NULL,
    [SupplierInvNo]     nvarchar(150)  NULL,
    [SupplierInvDate]   datetime       NULL,
    [rid]               nvarchar(40)   NULL
);

-- ============================================================
-- 5. VOUCHER SERIES REGISTRY (VoucherDetails_Table)
-- Required by some views and the connector factory resolution
-- ============================================================
IF OBJECT_ID('dbo.VoucherDetails_Table', 'U') IS NULL
CREATE TABLE [dbo].[VoucherDetails_Table] (
    [VoucherSeries]  nvarchar(125) NULL,
    [VoucherNo]      int           NULL,
    [Trans]          nvarchar(125) NULL,
    [TransactionName] nvarchar(125) NULL
);

-- ============================================================
-- SEED DATA
-- ============================================================

-- ---- Reset existing test data --------------------------------
-- Re-running this script should replace the connector test dataset
-- instead of duplicating voucher, PO, GRN, and invoice rows.
DELETE FROM [dbo].[VoucherDetails_Table];
DELETE FROM [dbo].[Transaction_Payments_Table];
DELETE FROM [dbo].[EFIMRDetailsTable];
DELETE FROM [dbo].[Transaction_ItemBody_Table];
DELETE FROM [dbo].[Transaction_Header_Table];
DELETE FROM [dbo].[Master_RegistrationDetails_Table];
DELETE FROM [dbo].[Master_MasterCodes_Table];
DELETE FROM [dbo].[Master_Table];

-- ---- Vendors ------------------------------------------------
-- Active vendors cover live cases plus documented reconciliation scenarios.
INSERT INTO [dbo].[Master_Table] (MasterName, MasterType, VoucherSeries, VoucherNo, VoucherLineNo, inactive) VALUES
    ('ACME Supplies Pvt Ltd', 'creditoraccount', 'App PO%', 1, 1, 'False'),
    ('BuildRight Materials', 'creditoraccount',  'App PO%', 2, 1, 'False'),
    ('DHANVEEN PIGMENTS PVT.LTD.', 'creditoraccount', 'App PO%', 616, 1, 'False'),
    ('Bhabani Pigments Pvt. Ltd.', 'creditoraccount', 'App PO%', 680, 1, 'False'),
    ('Azelis India Pvt Ltd', 'creditoraccount', 'App PO%', 13, 1, 'False'),
    ('AARJAVAM TECHFAB PVT LTD_HYD', 'creditoraccount', 'App PO%', 288, 1, 'False'),
    ('Spectrum Industrial Chemicals Ltd', 'creditoraccount', 'App PO%', 1010, 1, 'False'),
    ('Old Vendor Co',        'creditoraccount',  'App PO%', 3, 1, 'True');

INSERT INTO [dbo].[Master_MasterCodes_Table] (MasterName, MasterCode, mastertype, VoucherSeries, VoucherNo) VALUES
    ('ACME Supplies Pvt Ltd', 'VND001', 'creditoraccount', 'App PO%', 1),
    ('BuildRight Materials',  'VND002', 'creditoraccount', 'App PO%', 2),
    ('DHANVEEN PIGMENTS PVT.LTD.', 'VND616', 'creditoraccount', 'App PO%', 616),
    ('Bhabani Pigments Pvt. Ltd.', 'VND680', 'creditoraccount', 'App PO%', 680),
    ('Azelis India Pvt Ltd', 'VND013', 'creditoraccount', 'App PO%', 13),
    ('AARJAVAM TECHFAB PVT LTD_HYD', 'VND288', 'creditoraccount', 'App PO%', 288),
    ('Spectrum Industrial Chemicals Ltd', 'VND810', 'creditoraccount', 'App PO%', 1010),
    ('Old Vendor Co',         'VND003', 'creditoraccount', 'App PO%', 3);

INSERT INTO [dbo].[Master_RegistrationDetails_Table] (MasterName, GSTIN, PANo, VoucherSeries, VoucherNo) VALUES
    ('ACME Supplies Pvt Ltd', '27AABCU9603R1ZX', 'AABCU9603R',  'App PO%', 1),
    ('BuildRight Materials',  '29AADCB2230M1Z1', 'AADCB2230M',  'App PO%', 2),
    ('DHANVEEN PIGMENTS PVT.LTD.', '24AABCD0213A1ZT', 'AABCD0213A', 'App PO%', 616),
    ('Bhabani Pigments Pvt. Ltd.', '19AADCB3680P1Z5', 'AADCB3680P', 'App PO%', 680),
    ('Azelis India Pvt Ltd', '27AAECA0013R1ZV', 'AAECA0013R', 'App PO%', 13),
    ('AARJAVAM TECHFAB PVT LTD_HYD', '36AAUCA1090K1ZA', 'AAUCA1090K', 'App PO%', 288),
    ('Spectrum Industrial Chemicals Ltd', '24AAICS1010Q1ZT', 'AAICS1010Q', 'App PO%', 1010),
    ('Old Vendor Co',         NULL, NULL,                         'App PO%', 3);

-- ---- Purchase Orders ----------------------------------------
-- Three POs: two for ACME (different items), one for BuildRight
-- Voucher series 'App PO' matches the default connector series pattern

INSERT INTO [dbo].[Transaction_Header_Table]
    (VoucherSeries, VoucherNo, [Date], Account, PartyRefDoc, TotalBillValue, TotalNet, Currency, Remarks, TransactionName)
VALUES
    ('App PO', 1001, '2026-01-10', 'ACME Supplies Pvt Ltd',  'ACME-REF-2026-001', 150000.00, 150000.00, 'INR', 'Office supplies Q1', 'Purchase Order'),
    ('App PO', 1002, '2026-02-05', 'ACME Supplies Pvt Ltd',  'ACME-REF-2026-002', 75000.00,  75000.00,  'INR', 'IT equipment',        'Purchase Order'),
    ('App PO', 616,  '2026-04-05', 'DHANVEEN PIGMENTS PVT.LTD.', '616/2025-26', 1848000.00, 1848000.00, 'INR', 'Pigment Green - 7 for Blend Colours Pvt Ltd', 'Purchase Order'),
    ('App PO', 1003, '2026-02-20', 'BuildRight Materials',   'BR-PO-2026-01',     220000.00, 220000.00, 'INR', 'Construction raw mat','Purchase Order'),
    ('App PO', 680,  '2026-04-15', 'Bhabani Pigments Pvt. Ltd.', 'PO-KTD-680/2025-26', 4090050.00, 4090050.00, 'INR', 'Scenario LIVE-680: BPPL invoice recovery with two pigment lines', 'Purchase Order'),
    ('App PO', 13,   '2026-04-03', 'Azelis India Pvt Ltd', 'PO-BUR-13/2026-27', 191000.00, 191000.00, 'INR', 'Scenario LIVE-013: Azelis resin purchase order for existing case', 'Purchase Order'),
    ('App PO', 1004, '2026-03-12', 'BuildRight Materials', 'SCN-GRN-MISSING/2026-01', 96000.00, 96000.00, 'INR', 'Scenario GRN_NOT_FOUND: PO exists without any receipt rows', 'Purchase Order'),
    ('App PO', 1005, '2026-03-18', 'BuildRight Materials', 'SCN-OVER-RECEIPT/2026-01', 88000.00, 88000.00, 'INR', 'Scenario OVER_RECEIPT: GRN exceeds ordered quantity', 'Purchase Order'),
    ('App PO', 1006, '2026-03-22', 'Spectrum Industrial Chemicals Ltd', 'SCN-INVOICE-EXCEEDS/2026-01', 150000.00, 150000.00, 'INR', 'Scenario INVOICE_EXCEEDS: invoice quantity higher than received', 'Purchase Order'),
    ('App PO', 1007, '2026-01-05', 'ACME Supplies Pvt Ltd', 'SCN-DELAYED-RECEIPT/2026-01', 125000.00, 125000.00, 'INR', 'Scenario DELAYED_RECEIPT: late GRN after PO date', 'Purchase Order'),
    ('App PO', 1008, '2026-02-25', 'ACME Supplies Pvt Ltd', 'SCN-AUTO-CLOSE/2026-01', 100000.00, 100000.00, 'INR', 'Scenario AUTO_CLOSE: minor variance remains within tolerance band', 'Purchase Order'),
    ('App PO', 1010, '2026-03-28', 'Spectrum Industrial Chemicals Ltd', 'SCN-LLM-FUZZY/2026-01', 240000.00, 240000.00, 'INR', 'Scenario LLM_FUZZY: two lot-coded lines, identical qty/price -- invoice omits lot codes forcing LLM line disambiguation', 'Purchase Order'),
    -- PO 288: AARJAVAM TECHFAB -- mirrors the current live ATP/26-27/288 invoice.
    -- The invoice has no extracted PO number, so this is the vendor_search -> po_lookup recovery case.
    ('App PO', 288,  '2026-04-10', 'AARJAVAM TECHFAB PVT LTD_HYD', 'PO-HYD-288/2026-27', 156570.00, 156570.00, 'INR', 'Current live ATP/26-27/288 packing-bag invoice recovery scenario', 'Purchase Order');

-- PO line items
INSERT INTO [dbo].[Transaction_ItemBody_Table]
    (VoucherSeries, VoucherNo, VoucherLineNo, Code, Product, Unit, Quantity, Rate, Gross, Net, Description)
VALUES
    -- PO 1001 -- two lines
    ('App PO', 1001, 1, 'ITM-001', 'A4 Paper Ream',       'Box',    50,  300.00,  15000.00, 15000.00, 'A4 80gsm paper'),
    ('App PO', 1001, 2, 'ITM-002', 'Printer Cartridge HP','Each',  100, 1350.00, 135000.00,135000.00, 'HP 305 black'),
    -- PO 1002 -- single line
    ('App PO', 1002, 1, 'ITM-003', 'Laptop Dell Latitude', 'Each',    3,25000.00,  75000.00, 75000.00, 'Dell Latitude 5520'),
    -- PO 616/2025-26 -- single line matching case 13 invoice
    ('App PO', 616, 1, 'DVN-11062', 'Pigment Green - 7', 'Kg', 4000, 462.00, 1848000.00, 1848000.00, 'Pigment Green - 7, PRODUCT CODE : DVN-11062'),
    -- PO 1003 -- two lines
    ('App PO', 1003, 1, 'ITM-004', 'Cement OPC 53 Grade',  'Bag', 1000,   110.00, 110000.00,110000.00, 'OPC 53 grade 50kg bag'),
    ('App PO', 1003, 2, 'ITM-005', 'TMT Steel Bar 12mm',   'Kg',   500,   220.00, 110000.00,110000.00, 'Fe500 TMT bar'),
    -- PO 680 -- mirrors live case BPPL/2026-27/033
    ('App PO', 680, 1, 'BP-151', 'Alpha Blue (Pigment Blue 15.1)', 'Kg', 3000, 621.50, 1864500.00, 1864500.00, 'Alpha Blue (Pigment Blue 15.1), HSN 32041751'),
    ('App PO', 680, 2, 'BP-150', 'Alpha Blue (Pigment Blue 15.0)', 'Kg', 3700, 601.50, 2225550.00, 2225550.00, 'Alpha Blue (Pigment Blue 15.0), HSN 32041751'),
    -- PO 13 -- mirrors live case SI994099283
    ('App PO', 13, 1, 'RES-006', 'Resicor 006 Liquid GEN', 'Drum', 100, 1910.00, 191000.00, 191000.00, 'Resicor 006 Liquid GEN, 20 kg HDPE Drum pack'),
    -- Scenario GRN_NOT_FOUND
    ('App PO', 1004, 1, 'CEM-FAST', 'Fast Set Cement Additive', 'Bag', 800, 120.00, 96000.00, 96000.00, 'Fast set cement additive 25kg bag'),
    -- Scenario OVER_RECEIPT
    ('App PO', 1005, 1, 'STEEL-08', 'TMT Steel Bar 8mm', 'Kg', 800, 110.00, 88000.00, 88000.00, 'Fe500 TMT bar 8mm'),
    -- Scenario INVOICE_EXCEEDS
    ('App PO', 1006, 1, 'SOL-210', 'Industrial Solvent 210', 'Drum', 100, 1500.00, 150000.00, 150000.00, 'Industrial Solvent 210, 50L drum'),
    -- Scenario DELAYED_RECEIPT
    ('App PO', 1007, 1, 'PACK-001', 'Laminated Packing Roll', 'Roll', 500, 250.00, 125000.00, 125000.00, 'Laminated packing roll 24 inch'),
    -- Scenario AUTO_CLOSE
    ('App PO', 1008, 1, 'AUTO-001', 'Auto Close Test Chemical', 'Kg', 1000, 100.00, 100000.00, 100000.00, 'Auto close tolerance test chemical'),
    -- Scenario LLM_FUZZY / lot-coded PO lines, identical qty and price.
    -- The two lots are chemically equivalent batches; the only distinguisher
    -- is the lot suffix (A vs B).  The supplier's invoice omits lot codes,
    -- so both invoice lines carry the same description.  The deterministic
    -- scorer's gap between the two PO candidates is < AMBIGUITY_GAP (0.08),
    -- which classifies each invoice line as AMBIGUOUS and invokes LLM fallback.
    ('App PO', 1010, 1, 'RB-21A', 'Reactive Blue 21 Dye Lot A', 'Drum', 60, 2000.00, 120000.00, 120000.00, 'Reactive Blue 21 reactive textile dye Lot A 25 kg drum'),
    ('App PO', 1010, 2, 'RB-21B', 'Reactive Blue 21 Dye Lot B', 'Drum', 60, 2000.00, 120000.00, 120000.00, 'Reactive Blue 21 reactive textile dye Lot B 25 kg drum'),
    -- PO 288 -- AARJAVAM TECHFAB: current live ATP invoice lines.
    ('App PO', 288, 1, 'ARJ-BAG-2431', 'PP Bags 24x31 Palin 3+3 Gusset', 'Bag', 2500, 16.08, 113120.00, 113120.00, 'PP Bags 24x31 PALIN 3+3 GUSSET (HSN: 39232990)'),
    ('App PO', 288, 2, 'ARJ-BAG-2231', 'PP Bags 22x31 Blend 2.5+2.5 Gusset', 'Bag', 7000, 18.16, 42450.00, 42450.00, 'PP Bags 22x31 BLEND 2.5+2.5 GUSSET (HSN: 39232990)'),
    ('App PO', 288, 3, 'ARJ-FRT-001', 'Freight Outward (Bag)', 'Each', 1, 1000.00, 1000.00, 1000.00, 'FREIGHT OUTWARD(BAG)');

UPDATE [dbo].[Transaction_ItemBody_Table]
SET CostCentre = 'RM-COL-01',
    Department = 'Raw Materials'
WHERE VoucherSeries = 'App PO' AND VoucherNo = 616 AND VoucherLineNo = 1;

UPDATE [dbo].[Transaction_ItemBody_Table]
SET CostCentre = 'INFRA-HYD-01',
    Department = 'Infrastructure'
WHERE VoucherSeries = 'App PO' AND VoucherNo = 288;

-- ---- GRNs ---------------------------------------------------
-- GRNs received against PO 1001 (partial) and PO 1003 (full)
-- POrderNum links to VoucherNo in Transaction_Header_Table

INSERT INTO [dbo].[EFIMRDetailsTable]
    (PlantCode, GRNDATE, GRNNO, POrderNum, POrderLineNum,
     CURRENCYCODE, ItemCode, ItemDesc, ORDERQTY, GRNQTY,
     UNITPRICE, AMOUNT, PriceUnitCode,
     Suppcode, SuppName, WhouseCode, CURRATE, GRNPRICE, GRNVALUE, POrderDate)
VALUES
    -- GRN for PO 1001, line 1 (partial -- only 30 boxes received)
    ('MAIN', '2026-01-20', 'ABSR0001', 1001, 1,
     'INR', 'ITM-001', 'A4 80gsm paper', 50, 30,
     300.00, 9000.00, 'Box',
     'VND001', 'ACME Supplies Pvt Ltd', 'WH01', 1.0, 300.00, 9000.00, '2026-01-10'),
    -- GRN for PO 1001, line 2 (full)
    ('MAIN', '2026-01-20', 'ABSR0001', 1001, 2,
     'INR', 'ITM-002', 'HP 305 black', 100, 100,
     1350.00, 135000.00, 'Each',
     'VND001', 'ACME Supplies Pvt Ltd', 'WH01', 1.0, 1350.00, 135000.00, '2026-01-10'),
    -- GRN for PO 616/2025-26, line 1 (full)
    ('MAIN', '2026-04-09', 'ABSR0616', 616, 1,
     'INR', 'DVN-11062', 'Pigment Green - 7, PRODUCT CODE : DVN-11062', 4000, 4000,
     462.00, 1848000.00, 'Kg',
     'VND616', 'DHANVEEN PIGMENTS PVT.LTD.', 'WH03', 1.0, 462.00, 1848000.00, '2026-04-05'),
    -- GRN for PO 1003, line 1 (full)
    ('MAIN', '2026-03-05', 'ABSR0002', 1003, 1,
     'INR', 'ITM-004', 'OPC 53 grade 50kg bag', 1000, 1000,
     110.00, 110000.00, 'Bag',
     'VND002', 'BuildRight Materials', 'WH02', 1.0, 110.00, 110000.00, '2026-02-20'),
    -- GRN for PO 1003, line 2 (full)
    ('MAIN', '2026-03-05', 'ABSR0002', 1003, 2,
     'INR', 'ITM-005', 'Fe500 TMT bar', 500, 500,
     220.00, 110000.00, 'Kg',
        'VND002', 'BuildRight Materials', 'WH02', 1.0, 220.00, 110000.00, '2026-02-20'),
        -- GRN for PO 680, lines 1-2 (full) -- resolves live case PO lookup
        ('MAIN', '2026-04-18', 'ABSR0680', 680, 1,
        'INR', 'BP-151', 'Alpha Blue (Pigment Blue 15.1)', 3000, 3000,
        621.50, 1864500.00, 'Kg',
        'VND680', 'Bhabani Pigments Pvt. Ltd.', 'WH04', 1.0, 621.50, 1864500.00, '2026-04-15'),
        ('MAIN', '2026-04-18', 'ABSR0680', 680, 2,
        'INR', 'BP-150', 'Alpha Blue (Pigment Blue 15.0)', 3700, 3700,
        601.50, 2225550.00, 'Kg',
        'VND680', 'Bhabani Pigments Pvt. Ltd.', 'WH04', 1.0, 601.50, 2225550.00, '2026-04-15'),
        -- GRN for PO 13, line 1 (full) -- resolves live case PO lookup
        ('MAIN', '2026-04-07', 'ABSR0013', 13, 1,
        'INR', 'RES-006', 'Resicor 006 Liquid GEN, 20 kg HDPE Drum pack', 100, 100,
        1910.00, 191000.00, 'Drum',
        'VND013', 'Azelis India Pvt Ltd', 'WH05', 1.0, 1910.00, 191000.00, '2026-04-03'),
        -- No GRN rows for PO 1004 on purpose: GRN_NOT_FOUND scenario
        -- GRN for PO 1005 exceeds ordered quantity: OVER_RECEIPT
        ('MAIN', '2026-03-21', 'ABSR1005', 1005, 1,
        'INR', 'STEEL-08', 'Fe500 TMT bar 8mm', 800, 850,
        110.00, 93500.00, 'Kg',
        'VND002', 'BuildRight Materials', 'WH02', 1.0, 110.00, 93500.00, '2026-03-18'),
        -- GRN for PO 1006 under-received: INVOICE_EXCEEDS
        ('MAIN', '2026-03-25', 'ABSR1006', 1006, 1,
        'INR', 'SOL-210', 'Industrial Solvent 210, 50L drum', 100, 70,
        1500.00, 105000.00, 'Drum',
        'VND810', 'Spectrum Industrial Chemicals Ltd', 'WH06', 1.0, 1500.00, 105000.00, '2026-03-22'),
        -- GRN for PO 1007 arrives late: DELAYED_RECEIPT
        ('MAIN', '2026-02-28', 'ABSR1007', 1007, 1,
        'INR', 'PACK-001', 'Laminated packing roll 24 inch', 500, 500,
        250.00, 125000.00, 'Roll',
        'VND001', 'ACME Supplies Pvt Ltd', 'WH01', 1.0, 250.00, 125000.00, '2026-01-05'),
        -- GRN for PO 1008 exact receipt, invoice will carry only minor variance: AUTO_CLOSE
        ('MAIN', '2026-02-27', 'ABSR1008', 1008, 1,
        'INR', 'AUTO-001', 'Auto close tolerance test chemical', 1000, 1000,
        100.00, 100000.00, 'Kg',
        'VND001', 'ACME Supplies Pvt Ltd', 'WH01', 1.0, 100.00, 100000.00, '2026-02-25'),
        -- GRN for PO 1010: lot A and lot B both fully received (LLM_FUZZY scenario).
        -- GRN uses lot codes matching the PO; the supplier's invoice will NOT.
        ('MAIN', '2026-04-01', 'ABSR1010', 1010, 1,
        'INR', 'RB-21A', 'Reactive Blue 21 reactive textile dye Lot A 25 kg drum', 60, 60,
        2000.00, 120000.00, 'Drum',
        'VND810', 'Spectrum Industrial Chemicals Ltd', 'WH06', 1.0, 2000.00, 120000.00, '2026-03-28'),
        ('MAIN', '2026-04-01', 'ABSR1010', 1010, 2,
        'INR', 'RB-21B', 'Reactive Blue 21 reactive textile dye Lot B 25 kg drum', 60, 60,
        2000.00, 120000.00, 'Drum',
        'VND810', 'Spectrum Industrial Chemicals Ltd', 'WH06', 1.0, 2000.00, 120000.00, '2026-03-28'),
        -- GRN for PO 288, AARJAVAM TECHFAB (full receipt) -- mirrors the current ATP invoice.
        ('MAIN', '2026-04-10', 'ABSR0288', 288, 1,
        'INR', 'ARJ-BAG-2431', 'PP Bags 24x31 PALIN 3+3 GUSSET (HSN: 39232990)', 2500, 2500,
        16.08, 113120.00, 'Bag',
        'VND288', 'AARJAVAM TECHFAB PVT LTD_HYD', 'WH07', 1.0, 16.08, 113120.00, '2026-04-10'),
        ('MAIN', '2026-04-10', 'ABSR0288', 288, 2,
        'INR', 'ARJ-BAG-2231', 'PP Bags 22x31 BLEND 2.5+2.5 GUSSET (HSN: 39232990)', 7000, 7000,
        18.16, 42450.00, 'Bag',
        'VND288', 'AARJAVAM TECHFAB PVT LTD_HYD', 'WH07', 1.0, 18.16, 42450.00, '2026-04-10'),
        ('MAIN', '2026-04-10', 'ABSR0288', 288, 3,
        'INR', 'ARJ-FRT-001', 'FREIGHT OUTWARD(BAG)', 1, 1,
        1000.00, 1000.00, 'Each',
        'VND288', 'AARJAVAM TECHFAB PVT LTD_HYD', 'WH07', 1.0, 1000.00, 1000.00, '2026-04-10');

-- ---- Purchase Invoices (as vouchers, series 'App PI') --------
-- Invoice from ACME against PO 1001 (for the items received)
INSERT INTO [dbo].[Transaction_Header_Table]
    (VoucherSeries, VoucherNo, [Date], Account, PartyRefDoc, TotalBillValue, TotalNet, Currency, Remarks, TransactionName, GSTIN)
VALUES
    ('App PI', 2001, '2026-01-25', 'ACME Supplies Pvt Ltd', 'INV-ACME-2026-0045', 144000.00, 144000.00, 'INR', 'Invoice against PO 1001', 'Purchase Invoice', '27AABCU9603R1ZX'),
    ('App PI', 2616, '2026-04-11', 'DHANVEEN PIGMENTS PVT.LTD.', '90/26-27', 2180640.00, 1848000.00, 'INR', 'Invoice against PO 616/2025-26', 'Purchase Invoice', '24AABCD0213A1ZT'),
    ('App PI', 2002, '2026-03-10', 'BuildRight Materials',  'INV-BR-2026-0088',   220000.00, 220000.00, 'INR', 'Invoice against PO 1003', 'Purchase Invoice', '29AADCB2230M1Z1'),
    ('App PI', 2680, '2026-04-20', 'Bhabani Pigments Pvt. Ltd.', 'BPPL/2026-27/033', 4826259.00, 4090050.00, 'INR', 'Invoice against PO-KTD-680/2025-26', 'Purchase Invoice', '19AADCB3680P1Z5'),
    ('App PI', 2013, '2026-04-08', 'Azelis India Pvt Ltd', 'SI994099283', 225380.00, 191000.00, 'INR', 'Invoice against PO-BUR-13/2026-27', 'Purchase Invoice', '27AAECA0013R1ZV'),
    ('App PI', 2104, '2026-03-14', 'BuildRight Materials', 'INV-GRN-MISS-2104', 96000.00, 96000.00, 'INR', 'Invoice for GRN missing scenario', 'Purchase Invoice', '29AADCB2230M1Z1'),
    ('App PI', 2105, '2026-03-22', 'BuildRight Materials', 'INV-OVER-2105', 93500.00, 93500.00, 'INR', 'Invoice for over receipt scenario', 'Purchase Invoice', '29AADCB2230M1Z1'),
    ('App PI', 2106, '2026-03-26', 'Spectrum Industrial Chemicals Ltd', 'INV-EXCEEDS-2106', 150000.00, 150000.00, 'INR', 'Invoice exceeds received quantity scenario', 'Purchase Invoice', '24AAICS1010Q1ZT'),
    ('App PI', 2107, '2026-02-28', 'ACME Supplies Pvt Ltd', 'INV-DELAY-2107', 125000.00, 125000.00, 'INR', 'Invoice for delayed receipt scenario', 'Purchase Invoice', '27AABCU9603R1ZX'),
    ('App PI', 2108, '2026-02-28', 'ACME Supplies Pvt Ltd', 'INV-AUTO-2108', 100200.00, 100200.00, 'INR', 'Invoice within auto-close band', 'Purchase Invoice', '27AABCU9603R1ZX'),
    ('App PI', 2110, '2026-04-02', 'Spectrum Industrial Chemicals Ltd', 'INV-LLM-2110', 240000.00, 240000.00, 'INR', 'Invoice for fuzzy line matching scenario', 'Purchase Invoice', '24AAICS1010Q1ZT'),
    -- Invoice 2288: AARJAVAM TECHFAB -- mirrors the current live ATP/26-27/288 invoice.
    -- It intentionally carries no PO number in the extracted app-side case.
    ('App PI', 2288, '2026-04-17', 'AARJAVAM TECHFAB PVT LTD_HYD', 'ATP/26-27/288', 184753.00, 156570.00, 'INR', 'Current live ATP packing-bag invoice against ERP PO-HYD-288/2026-27', 'Purchase Invoice', '36AAUCA1090K1ZA');

-- Invoice line items
INSERT INTO [dbo].[Transaction_ItemBody_Table]
    (VoucherSeries, VoucherNo, VoucherLineNo, Code, Product, Unit, Quantity, Rate, Gross, Net, Description)
VALUES
    -- Invoice 2001 lines
    ('App PI', 2001, 1, 'ITM-001', 'A4 Paper Ream',       'Box',    30,   300.00,  9000.00,  9000.00, 'A4 80gsm paper'),
    ('App PI', 2001, 2, 'ITM-002', 'Printer Cartridge HP','Each',  100,  1350.00,135000.00,135000.00, 'HP 305 black'),
    -- Invoice 2616 line matching case 13 invoice
    ('App PI', 2616, 1, 'DVN-11062', 'Pigment Green - 7', 'Kg', 4000, 462.00, 1848000.00, 1848000.00, 'Pigment Green - 7, PRODUCT CODE : DVN-11062'),
    -- Invoice 2002 lines
    ('App PI', 2002, 1, 'ITM-004', 'Cement OPC 53 Grade', 'Bag',  1000,   110.00,110000.00,110000.00, 'OPC 53 grade 50kg bag'),
    ('App PI', 2002, 2, 'ITM-005', 'TMT Steel Bar 12mm',  'Kg',    500,   220.00,110000.00,110000.00, 'Fe500 TMT bar'),
    -- Invoice 2680 mirrors live BPPL extraction
    ('App PI', 2680, 1, 'BP-151', 'Alpha Blue (Pigment Blue 15.1)', 'Kg', 3000, 621.50, 1864500.00, 1864500.00, 'Alpha Blue (Pigment Blue 15.1), HSN: 32041751'),
    ('App PI', 2680, 2, 'BP-150', 'Alpha Blue (Pigment Blue 15.0)', 'Kg', 3700, 601.50, 2225550.00, 2225550.00, 'Alpha Blue (Pigment Blue 15.0), HSN: 32041751'),
    -- Invoice 2013 mirrors live Azelis extraction
    ('App PI', 2013, 1, 'RES-006', 'Resicor 006 Liquid GEN', 'Drum', 100, 1910.00, 191000.00, 191000.00, 'Resicor 006 Liquid GEN, 20 kg HDPE Drum (5)'),
    -- Scenario GRN_NOT_FOUND
    ('App PI', 2104, 1, 'CEM-FAST', 'Fast Set Cement Additive', 'Bag', 800, 120.00, 96000.00, 96000.00, 'Fast set cement additive 25kg bag'),
    -- Scenario OVER_RECEIPT
    ('App PI', 2105, 1, 'STEEL-08', 'TMT Steel Bar 8mm', 'Kg', 850, 110.00, 93500.00, 93500.00, 'Fe500 TMT bar 8mm'),
    -- Scenario INVOICE_EXCEEDS
    ('App PI', 2106, 1, 'SOL-210', 'Industrial Solvent 210', 'Drum', 100, 1500.00, 150000.00, 150000.00, 'Industrial Solvent 210, 50L drum'),
    -- Scenario DELAYED_RECEIPT
    ('App PI', 2107, 1, 'PACK-001', 'Laminated Packing Roll', 'Roll', 500, 250.00, 125000.00, 125000.00, 'Laminated packing roll 24 inch'),
    -- Scenario AUTO_CLOSE: 0.2 percent quantity delta, still within tolerance band
    ('App PI', 2108, 1, 'AUTO-001', 'Auto Close Test Chemical', 'Kg', 1002, 100.00, 100200.00, 100200.00, 'Auto close tolerance test chemical'),
    -- Scenario LLM_FUZZY: invoice omits lot codes and uses identical descriptions
    -- for both lines.  Item codes are blank (supplier codes differ from buyer codes).
    -- Scoring analysis vs PO 1010:
    --   Both lines: token_sim ~0.78 vs Lot A / ~0.78 vs Lot B (only "A"/"B" differs)
    --   qty=60, price=2000, amount=120000, uom=Drum all equal for both candidates
    --   Result: gap (best - second_best) < AMBIGUITY_GAP (0.08) -> STATUS_AMBIGUOUS
    --   -> LineMatchLLMFallbackService invoked to resolve each line
    ('App PI', 2110, 1, '', 'Reactive Blue 21 Dye', 'Drum', 60, 2000.00, 120000.00, 120000.00, 'Reactive Blue 21 reactive dye 25 kg drum'),
    ('App PI', 2110, 2, '', 'Reactive Blue 21 Dye', 'Drum', 60, 2000.00, 120000.00, 120000.00, 'Reactive Blue 21 reactive dye 25 kg drum'),
    -- Invoice 2288 -- AARJAVAM TECHFAB packing-bag lines matching the live ATP invoice.
    -- Codes are blank to mirror the current extracted invoice and force description-based matching.
    ('App PI', 2288, 1, '', 'PP Bags 24x31 Palin 3+3 Gusset', 'Bag', 2500, 16.08, 113120.00, 113120.00, 'PP Bags 24x31 PALIN 3+3 GUSSET (HSN: 39232990)'),
    ('App PI', 2288, 2, '', 'PP Bags 22x31 Blend 2.5+2.5 Gusset', 'Bag', 7000, 18.16, 42450.00, 42450.00, 'PP Bags 22x31 BLEND 2.5+2.5 GUSSET (HSN: 39232990)'),
    ('App PI', 2288, 3, '', 'Freight Outward (Bag)', 'Each', 1, 1000.00, 1000.00, 1000.00, 'FREIGHT OUTWARD(BAG)');

UPDATE [dbo].[Transaction_ItemBody_Table]
SET CostCentre = 'RM-COL-01',
    Department = 'Raw Materials'
WHERE VoucherSeries = 'App PI' AND VoucherNo = 2616 AND VoucherLineNo = 1;

-- ---- Invoice payments (used for duplicate invoice detection) -
-- Supplier invoice numbers as recorded by the AP team
INSERT INTO [dbo].[Transaction_Payments_Table]
    (VoucherSeries, VoucherNo, VoucherLineNo, PaymentMethod, PartyAccount, Amount, NetAmount, SupplierInvNo, SupplierInvDate, TVoucherSeries, TVoucherNo)
VALUES
    -- Payment entry for invoice 2001
    ('App PI', 2001, 1, 'Credit', 'ACME Supplies Pvt Ltd', 144000.00, 144000.00, 'INV-ACME-2026-0045', '2026-01-25', 'App PI', 2001),
    -- Payment entry for invoice 2616
    ('App PI', 2616, 1, 'Credit', 'DHANVEEN PIGMENTS PVT.LTD.', 2180640.00, 2180640.00, '90/26-27', '2026-04-11', 'App PI', 2616),
    -- Payment entry for invoice 2002
    ('App PI', 2002, 1, 'Credit', 'BuildRight Materials',  220000.00, 220000.00, 'INV-BR-2026-0088',   '2026-03-10', 'App PI', 2002),
    ('App PI', 2680, 1, 'Credit', 'Bhabani Pigments Pvt. Ltd.', 4826259.00, 4826259.00, 'BPPL/2026-27/033', '2026-04-20', 'App PI', 2680),
    ('App PI', 2013, 1, 'Credit', 'Azelis India Pvt Ltd', 225380.00, 225380.00, 'SI994099283', '2026-04-08', 'App PI', 2013),
    ('App PI', 2104, 1, 'Credit', 'BuildRight Materials', 96000.00, 96000.00, 'INV-GRN-MISS-2104', '2026-03-14', 'App PI', 2104),
    ('App PI', 2105, 1, 'Credit', 'BuildRight Materials', 93500.00, 93500.00, 'INV-OVER-2105', '2026-03-22', 'App PI', 2105),
    ('App PI', 2106, 1, 'Credit', 'Spectrum Industrial Chemicals Ltd', 150000.00, 150000.00, 'INV-EXCEEDS-2106', '2026-03-26', 'App PI', 2106),
    ('App PI', 2107, 1, 'Credit', 'ACME Supplies Pvt Ltd', 125000.00, 125000.00, 'INV-DELAY-2107', '2026-02-28', 'App PI', 2107),
    ('App PI', 2108, 1, 'Credit', 'ACME Supplies Pvt Ltd', 100200.00, 100200.00, 'INV-AUTO-2108', '2026-02-28', 'App PI', 2108),
    ('App PI', 2110, 1, 'Credit', 'Spectrum Industrial Chemicals Ltd', 240000.00, 240000.00, 'INV-LLM-2110', '2026-04-02', 'App PI', 2110),
    -- Payment entry for AARJAVAM invoice 2288
    ('App PI', 2288, 1, 'Credit', 'AARJAVAM TECHFAB PVT LTD_HYD', 184753.00, 184753.00, 'ATP/26-27/288', '2026-04-17', 'App PI', 2288),
    -- A duplicate invoice (same supplier inv number, different voucher) -- tests the duplicate_check query
    ('App PI', 2003, 1, 'Credit', 'ACME Supplies Pvt Ltd', 144000.00, 144000.00, 'INV-ACME-2026-0045', '2026-01-25', 'App PI', 2003);

-- ---- Voucher series registry --------------------------------
INSERT INTO [dbo].[VoucherDetails_Table] (VoucherSeries, Trans, TransactionName) VALUES
    ('App PO', 'PurchaseOrder',   'Purchase Order'),
    ('App PI', 'PurchaseInvoice', 'Purchase Invoice'),
    ('ABSR',   'GRN',             'Goods Receipt Note');

-- ============================================================
-- VERIFICATION QUERIES
-- Run these to confirm data is correct after import
-- ============================================================

-- Should return 2 active vendors
SELECT MasterName, MasterType FROM Master_Table WHERE inactive = 'False' AND MasterType LIKE '%creditor%';

-- Should return PO 1001 for vendor ACME
SELECT VoucherSeries, VoucherNo, [Date], Account, TotalBillValue
FROM Transaction_Header_Table
WHERE VoucherSeries LIKE 'App PO%' AND VoucherNo = 1001;

-- Should return the case 13 PO using the external PO number format
SELECT VoucherSeries, VoucherNo, PartyRefDoc, [Date], Account, TotalBillValue
FROM Transaction_Header_Table
WHERE VoucherSeries LIKE 'App PO%' AND PartyRefDoc = '616/2025-26';

-- Should return GRN ABSR0001 linked to PO 1001
SELECT GRNNO, GRNDATE, POrderNum, SuppName, SUM(GRNVALUE) AS total_grn_value
FROM EFIMRDetailsTable WHERE POrderNum = 1001
GROUP BY GRNNO, GRNDATE, POrderNum, SuppName;

-- Should detect duplicate: both 2001 and 2003 share SupplierInvNo
SELECT SupplierInvNo, PartyAccount, COUNT(*) AS occurrences
FROM Transaction_Payments_Table
GROUP BY SupplierInvNo, PartyAccount
HAVING COUNT(*) > 1;

-- Should return the case 13 invoice payment reference
SELECT VoucherSeries, VoucherNo, PartyAccount, SupplierInvNo, SupplierInvDate, Amount
FROM Transaction_Payments_Table
WHERE SupplierInvNo = '90/26-27';

-- Should return all scenario-tagged POs added for live and LLM-driven matching coverage
SELECT VoucherNo, PartyRefDoc, Account, Remarks
FROM Transaction_Header_Table
WHERE VoucherSeries = 'App PO'
    AND (
            VoucherNo IN (13, 288, 680, 1004, 1005, 1006, 1007, 1008, 1010)
            OR PartyRefDoc IN (
                    'PO-KTD-680/2025-26',
                    'PO-BUR-13/2026-27',
                    'PO-HYD-288/2026-27',
                    'SCN-GRN-MISSING/2026-01',
                    'SCN-OVER-RECEIPT/2026-01',
                    'SCN-INVOICE-EXCEEDS/2026-01',
                    'SCN-DELAYED-RECEIPT/2026-01',
                    'SCN-AUTO-CLOSE/2026-01',
                    'SCN-LLM-FUZZY/2026-01'
            )
    )
ORDER BY VoucherNo;

-- Should confirm the intentional GRN gap for the GRN_NOT_FOUND scenario
SELECT COUNT(*) AS grn_rows_for_1004
FROM EFIMRDetailsTable
WHERE POrderNum = 1004;

-- Should show over-receipt and under-receipt conditions for scenario validation
SELECT POrderNum, POrderLineNum, ORDERQTY, GRNQTY, ItemCode
FROM EFIMRDetailsTable
WHERE POrderNum IN (1005, 1006)
ORDER BY POrderNum, POrderLineNum;
