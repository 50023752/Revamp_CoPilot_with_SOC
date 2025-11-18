============================================================
Testing ADK BigQueryToolset Availability
============================================================

[Test 1] Checking BigQueryToolset imports...
✅ BigQueryToolset imported successfully!
   Location: google.adk.tools.bigquery.BigQueryToolset

   Available methods:
   - close
   - from_config
   - get_tools
   - get_tools_with_prefix
   - process_llm_request

[Test 2] Attempting to initialize BigQueryToolset...
/home/yashchaudhary/Revamp_CoPilot_with_SOC/test_bigquery_toolset.py:68: UserWarning: [EXPERIMENTAL] BigQueryCredentialsConfig: This feature is experimental and may change or be removed in future versions without notice. It may introduce breaking changes at any time.
  credentials_config = BigQueryCredentialsConfig(credentials=credentials)
/home/yashchaudhary/.local/lib/python3.12/site-packages/google/adk/utils/feature_decorator.py:87: UserWarning: [EXPERIMENTAL] BaseGoogleCredentialsConfig: This feature is experimental and may change or be removed in future versions without notice. It may introduce breaking changes at any time.
  return orig_init(self, *args, **kwargs)
/home/yashchaudhary/Revamp_CoPilot_with_SOC/test_bigquery_toolset.py:71: UserWarning: [EXPERIMENTAL] BigQueryToolset: This feature is experimental and may change or be removed in future versions without notice. It may introduce breaking changes at any time.
  toolset = BigQueryToolset(
/home/yashchaudhary/.local/lib/python3.12/site-packages/google/adk/tools/bigquery/bigquery_toolset.py:50: UserWarning: [EXPERIMENTAL] BigQueryToolConfig: Config defaults may have breaking change in the future.
  bigquery_tool_config if bigquery_tool_config else BigQueryToolConfig()
✅ BigQueryToolset initialized successfully!
   Project: analytics-datapipeline-prod
   Dataset: aiml_cj_nostd_mart

[Test 3] Checking available tools...
/home/yashchaudhary/.local/lib/python3.12/site-packages/google/adk/tools/bigquery/bigquery_toolset.py:73: UserWarning: [EXPERIMENTAL] GoogleTool: This feature is experimental and may change or be removed in future versions without notice. It may introduce breaking changes at any time.
  GoogleTool(
✅ get_tools() works!
   Available tools: 10
     - get_dataset_info
     - get_table_info
     - list_dataset_ids

[Test 4] Note: BigQueryToolset is designed for LLM tool-calling...
   For direct schema access, use direct BigQuery client (Test 5)

[Test 5] (skipped - direct client tested below)

[Test 6] Testing Direct BigQuery Client (Fallback)...
✅ Direct BigQuery Client works!
   Table: TW_NOSTD_MART_HIST
   Columns: 119
   First 5 columns:
     - Net_Bounce_Flag: INTEGER
     - MOB_ON_INSTL_START_DATE: INTEGER
     - MOB_3M_Flag: INTEGER
     - MOB_3M_EVER_0PLUS_Flag: INTEGER
     - MOB_6M_Flag: INTEGER

[Test 7] Testing Column Descriptions from INFORMATION_SCHEMA...
✅ INFORMATION_SCHEMA query works!
   Table: TW_NOSTD_MART_HIST
   Columns with descriptions: 119
   Sample descriptions (first 5):
     - Net_Bounce_Flag: Indicates if the payment has bounced for the current month (...
     - MOB_ON_INSTL_START_DATE: The month on book from the installment start date. For examp...
     - MOB_3M_Flag: Indicates if the customer has completed 3 months on the book...
     - MOB_3M_EVER_0PLUS_Flag: Indicates if the customer has been 0+ days past due in the l...
     - MOB_6M_Flag: Indicates if the customer has completed 6 months on the book...

============================================================
Testing Complete
============================================================