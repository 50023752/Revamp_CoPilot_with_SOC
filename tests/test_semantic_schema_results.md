================================================================================
PHASE 1: Semantic Schema Enhancement Test
================================================================================

[Step 1] Fetching Semantic Schema for Collections Table...
   Project: analytics-datapipeline-prod
   Dataset: aiml_cj_nostd_mart
   Table: TW_NOSTD_MART_HIST

✅ Semantic Schema Retrieved Successfully!

# Collections Table Schema - Semantic View

**Use this to understand available fields grouped by business purpose**

## 🔑 Identity & Agreement Fields
  AGREEMENTNO: STRING | Unique identifier for the loan agreement, typically an alphanumeric code. [Key field]
  BRANCHID: NUMERIC | Unique identifier for the branch where the loan was originated. [Key field]
  SUPPLIERID: NUMERIC | The unique identifier for the supplier. [Key field]

## 📅 Time & Temporal Fields
  MOB_ON_INSTL_START_DATE: INTEGER | The month on book from the installment start date. For example, a value of 1 indicates the account's first EMI month.
  DISBURSALDATE: DATE | The date when the loan amount was disbursed. [Date field]
  MATURITYDATE: DATE | The scheduled date when the loan will be fully paid off. [Date field]
  PAYMENT_DATE: DATE | The date of the payment. [Date field]
  BUSINESS_DATE: DATE | The operational date for which the data is relevant. This is the primary date field for filtering time-series data. [Date field]

## 🎯 Performance Cohorts (3M/6M/12M)
  MOB_3M_Flag: INTEGER | Indicates if the customer has completed 3 months on the book (1 for yes, 0 for no).
  MOB_3M_EVER_0PLUS_Flag: INTEGER | Indicates if the customer has been 0+ days past due in the last 3 months (1 for yes, 0 for no).
  MOB_6M_Flag: INTEGER | Indicates if the customer has completed 6 months on the book (1 for yes, 0 for no).
  MOB_6M_EVER_30PLUS_Flag: INTEGER | Indicates if the customer has been 30+ days past due in the last 6 months (1 for yes, 0 for no).
  MOB_12M_Flag: INTEGER | Indicates if the customer has completed 12 months on the book (1 for yes, 0 for no).
  MOB_12M_EVER_30PLUS_Flag: INTEGER | Indicates if the customer has been 30+ days past due in the last 12 months (1 for yes, 0 for no).
  MOB_12M_EVER_60PLUS_Flag: INTEGER | Indicates if the customer has been 60+ days past due in the last 12 months (1 for yes, 0 for no).
  MOB_6M_EVER_90PLUS_Flag: INTEGER | Indicates if the customer has been 90+ days past due in the last 6 months (1 for yes, 0 for no).
  MOB_6M_EVER_0PLUS_Flag: INTEGER | Indicates if the customer has been 0+ days past due in the last 6 months (1 for yes, 0 for no).
  MOB_12M_EVER_90PLUS_Flag: INTEGER | Indicates if the customer has been 90+ days past due in the last 12 months (1 for yes, 0 for no).
  RISK_MOB_6M_Flag: INTEGER | Indicates if the customer is risky in the last 6 months (1 for yes, 0 for no).
  RISK_MOB_12M_Flag: INTEGER | Indicates if the customer is risky in the last 12 months (1 for yes, 0 for no).
  RISK_MOB_6M_EVER_30PLUS_Flag: INTEGER | Indicates if the customer has been 30+ days past due in the last 6 months (1 for yes, 0 for no).
  RISK_MOB_12M_EVER_30PLUS_Flag: INTEGER | Indicates if the customer has been 30+ days past due in the last 12 months (1 for yes, 0 for no).
  RISK_MOB_12M_EVER_90PLUS_Flag: INTEGER | Indicates if the customer has been 90+ days past due in the last 12 months (1 for yes, 0 for no).

## 📊 Delinquency & DPD Metrics
  Delinquency_1_30_LY_Flag: INTEGER | Indicates if the customer was 1-30 days past due last year (1 for yes, 0 for no).
  Delinquency_30_60_LY_Flag: INTEGER | Indicates if the customer was 30-60 days past due last year (1 for yes, 0 for no).
  Delinquency_60_90_LY_Flag: INTEGER | Indicates if the customer was 60-90 days past due last year (1 for yes, 0 for no).
  Delinquency_0LY_90PLUSCurrent_SLIPPAGE_Flag: INTEGER | Indicates if the customer has slipped from 0 days past due last year to 90+ days past due currently (1 for yes, 0 for no).
  Delinquency_1_30LY_90PLUSCurrent_SLIPPAGE_Flag: INTEGER | Indicates if the customer has slipped from 1-30 days past due last year to 90+ days past due currently (1 for yes, 0 for no).
  Delinquency_31_60LY_90PLUSCurrent_SLIPPAGE_Flag: INTEGER | Indicates if the customer has slipped from 31-60 days past due last year to 90+ days past due currently (1 for yes, 0 for no).
  Delinquency_61_90LY_90PLUSCurrent_SLIPPAGE_Flag: INTEGER | Indicates if the customer has slipped from 61-90 days past due last year to 90+ days past due currently (1 for yes, 0 for no).
  SOM_DPD: NUMERIC | The number of days past due at the start of the month.
  SOM_DPD_BUCKET: NUMERIC | Indicates the DPD (Days Past Due) bucket of the customer as of the start of the current month. CRITICAL RULE: For analysis, you MUST use these exact buckets: '0', '1-2', '3-5', '6-10', and '11+'.
  Delinquency_0_Flag: INTEGER | Indicates if the customer is currently 0 days past due (1 for yes, 0 for no).
  Delinquency_30_Flag: INTEGER | Indicates if the customer is currently 30 days past due (1 for yes, 0 for no).
  Delinquency_60_Flag: INTEGER | Indicates if the customer is currently 60 days past due (1 for yes, 0 for no).
  Delinquency_90_Flag: INTEGER | Indicates if the customer is currently 90 days past due (1 for yes, 0 for no).
  Delinquency_0_LY_Flag: INTEGER | Indicates if the customer was 0 days past due last year (1 for yes, 0 for no).
  Delinquency_30_LY_Flag: INTEGER | Indicates if the customer was 30 days past due last year (1 for yes, 0 for no).
  Delinquency_60_LY_Flag: INTEGER | Indicates if the customer was 60 days past due last year (1 for yes, 0 for no).
  Delinquency_90_LY_Flag: INTEGER | Indicates if the customer was 90 days past due last year (1 for yes, 0 for no).
  Delinquency_0_PLUS_1YEAR_SLIPPAGE_Flag: INTEGER | Indicates if the customer has slipped from 0+ days past due to 1 year.
  Delinquency_30_PLUS_1YEAR_SLIPPAGE_Flag: INTEGER | Indicates if the customer has slipped from 30+ days past due to 1 year.
  Delinquency_60_PLUS_1YEAR_SLIPPAGE_Flag: INTEGER | Indicates if the customer has slipped from 60+ days past due to 1 year.
  Delinquency_90_PLUS_1YEAR_SLIPPAGE_Flag: INTEGER | Indicates if the customer has slipped from 90+ days past due to 1 year.

## 💰 Amount & Financial Fields
  SOM_EMIDEBTOR: NUMERIC | The EMI outstanding at the start of the month.
  SOM_POS: NUMERIC | Start of Month Principal Outstanding - the outstanding loan amount at the start of the month.
  SOM_TOTAL_CHARGES_DUE: NUMERIC | The total charges due from the customer at the start of the month.
  AMOUNTFINANCE: NUMERIC | The total amount financed, including principal and possibly other capitalized fees.
  ASSETCOST: NUMERIC | The total cost of the asset financed.
  POS: NUMERIC | Principal Outstanding - the current remaining principal balance on the loan.
  TOTAL_CHARGES_DUE: NUMERIC | The total charges due from the customer.
  NO_OF_EMI_PAID: INTEGER | The number of EMIs paid by the customer. [Key field]
  NO_EMI_DUE: INTEGER | The number of EMIs due.
  TOTAL_NO_OF_EMI: INTEGER | The total number of EMIs for the loan.
  EMI_COLLECTION: NUMERIC | The amount of EMI collected.
  CBC_CHARGES_COLLECTION: NUMERIC | The amount of cheque bounce charges collected.
  LPP_CHARGES_COLLECTION: NUMERIC | The amount of late payment penalty charges collected.
  PAYMENT_AMOUNT: NUMERIC | The amount of the payment.
  Final_COLLECTION_AMOUNT: NUMERIC | The final amount collected from the customer.

## ✅ Collections Status & Flags
  ODC_COLLECTION: NUMERIC | The amount of overdue charges collected.
  CBC_COLLECTION: NUMERIC | The amount of cheque bounce charges collected.
  EXCESSMONEY_COLLECTION: NUMERIC | The amount of excess money collected.
  GNS1: STRING | Indicates if a customer bounced on their first EMI ('Y'/'N'). CRITICAL RULE: When a user asks for 'GNS1', you MUST use this column and MUST apply the filter MOB_ON_INSTL_START_DATE = 1
  NNS1: STRING | NNS for 1st EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 1st month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 1.
  GNS2: STRING | GNS for 2nd EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 2nd month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 2.
  NNS2: STRING | NNS for 2nd EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 2nd month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 2.
  GNS3: STRING | GNS for 3rd EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 3rd month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 3.
  NNS3: STRING | NNS for 3rd EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 3rd month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 3.
  GNS4: STRING | GNS for 4th EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 4th month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 4.
  NNS4: STRING | NNS for 4th EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 4th month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 4.
  GNS5: STRING | GNS for 5th EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 5th month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 5.
  NNS5: STRING | NNS for 5th EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 5th month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 5.
  GNS6: STRING | GNS for 6th EMI ('Y'/'N'). CRITICAL RULE: This is only valid for accounts in their 6th month. Any calculation MUST be filtered for MOB_ON_INSTL_START_DATE = 6.