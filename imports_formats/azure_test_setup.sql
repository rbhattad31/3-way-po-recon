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
-- Two vendors: one active supplier, one inactive
INSERT INTO [dbo].[Master_Table] (MasterName, MasterType, VoucherSeries, VoucherNo, VoucherLineNo, inactive) VALUES
    ('ACME Supplies Pvt Ltd', 'creditoraccount', 'App PO%', 1, 1, 'False'),
    ('BuildRight Materials', 'creditoraccount',  'App PO%', 2, 1, 'False'),
    ('DHANVEEN PIGMENTS PVT.LTD.', 'creditoraccount', 'App PO%', 616, 1, 'False'),
    ('Old Vendor Co',        'creditoraccount',  'App PO%', 3, 1, 'True');

INSERT INTO [dbo].[Master_MasterCodes_Table] (MasterName, MasterCode, mastertype, VoucherSeries, VoucherNo) VALUES
    ('ACME Supplies Pvt Ltd', 'VND001', 'creditoraccount', 'App PO%', 1),
    ('BuildRight Materials',  'VND002', 'creditoraccount', 'App PO%', 2),
    ('DHANVEEN PIGMENTS PVT.LTD.', 'VND616', 'creditoraccount', 'App PO%', 616),
    ('Old Vendor Co',         'VND003', 'creditoraccount', 'App PO%', 3);

INSERT INTO [dbo].[Master_RegistrationDetails_Table] (MasterName, GSTIN, PANo, VoucherSeries, VoucherNo) VALUES
    ('ACME Supplies Pvt Ltd', '27AABCU9603R1ZX', 'AABCU9603R',  'App PO%', 1),
    ('BuildRight Materials',  '29AADCB2230M1Z1', 'AADCB2230M',  'App PO%', 2),
    ('DHANVEEN PIGMENTS PVT.LTD.', '24AABCD0213A1ZT', 'AABCD0213A', 'App PO%', 616),
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
    ('App PO', 1003, '2026-02-20', 'BuildRight Materials',   'BR-PO-2026-01',     220000.00, 220000.00, 'INR', 'Construction raw mat','Purchase Order');

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
    ('App PO', 1003, 2, 'ITM-005', 'TMT Steel Bar 12mm',   'Kg',   500,   220.00, 110000.00,110000.00, 'Fe500 TMT bar');

UPDATE [dbo].[Transaction_ItemBody_Table]
SET CostCentre = 'RM-COL-01',
    Department = 'Raw Materials'
WHERE VoucherSeries = 'App PO' AND VoucherNo = 616 AND VoucherLineNo = 1;

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
     'VND002', 'BuildRight Materials', 'WH02', 1.0, 220.00, 110000.00, '2026-02-20');

-- ---- Purchase Invoices (as vouchers, series 'App PI') --------
-- Invoice from ACME against PO 1001 (for the items received)
INSERT INTO [dbo].[Transaction_Header_Table]
    (VoucherSeries, VoucherNo, [Date], Account, PartyRefDoc, TotalBillValue, TotalNet, Currency, Remarks, TransactionName, GSTIN)
VALUES
    ('App PI', 2001, '2026-01-25', 'ACME Supplies Pvt Ltd', 'INV-ACME-2026-0045', 144000.00, 144000.00, 'INR', 'Invoice against PO 1001', 'Purchase Invoice', '27AABCU9603R1ZX'),
    ('App PI', 2616, '2026-04-11', 'DHANVEEN PIGMENTS PVT.LTD.', '90/26-27', 2180640.00, 1848000.00, 'INR', 'Invoice against PO 616/2025-26', 'Purchase Invoice', '24AABCD0213A1ZT'),
    ('App PI', 2002, '2026-03-10', 'BuildRight Materials',  'INV-BR-2026-0088',   220000.00, 220000.00, 'INR', 'Invoice against PO 1003', 'Purchase Invoice', '29AADCB2230M1Z1');

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
    ('App PI', 2002, 2, 'ITM-005', 'TMT Steel Bar 12mm',  'Kg',    500,   220.00,110000.00,110000.00, 'Fe500 TMT bar');

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
