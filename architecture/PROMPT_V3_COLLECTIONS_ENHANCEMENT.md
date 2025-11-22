 Prompt Optimized Successfully
Your Collections Agent prompt has been updated with 7 critical fixes and 5 enhancements:

Critical Fixes Applied:
🔴 Date Anchoring Bug Fixed

Before: DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH) ❌ (fails with historical data)
After: DATE_SUB((SELECT MAX(BUSINESS_DATE) FROM table), INTERVAL 3 MONTH) ✅
Impact: Queries now work correctly even when data is days/weeks old
🔴 Syntax Error Removed

Before: WHERE WHERE BUSINESS_DATE... (double WHERE in GNS1 example)
After: Single WHERE with proper filtering
Impact: Example SQL now executes without syntax errors
🔴 Roll Forward Logic Corrected

Before: "If difference = 1" (too restrictive)
After: (DPD_BUCKET - SOM_DPD_BUCKET) > 0 (any forward movement)
Impact: Captures all worsening accounts (1→2, 1→3, 1→4, etc.)
🔴 GNS Filter Completed

Before: Missing AND GNS[x] = 'Y' check
After: Full filter: MOB = x AND Bounce_Flag = 'Y' AND GNS[x] = 'Y'
Impact: Accurate GNS metrics (was over-counting before)
🔴 Roll Back SQL Added

Before: Only "Thought" for roll back, no SQL example
After: Complete working SQL query
Impact: LLM now has concrete pattern for roll back queries
🔴 Hardcoded Table Removed

Before: 0+ DPD example used literal table name
After: Uses {settings.gcp_project_id}... variables
Impact: Works across environments (dev/prod)
🔴 XBKT Clarified

Before: Just listed filter, no context
After: "Regular accounts that just bounced"
Impact: LLM understands business intent, not just syntax
Enhancements Added:
Vintage Analysis Structured

Added explicit GROUP BY guidance (DISBURSAL_DATE, MOB_ON_INSTL_START_DATE)
Formula clarity: SUM(NR_x) / SUM(DR_x)
Business Logic Table

Consolidated all roll movements with formulas
Added STABLE and NORMALIZATION definitions
DECLARE Pattern for Consistency

All examples now use DECLARE max_date pattern
Prevents subquery repetition
Efficiency Rule

Added: "Do not use SELECT *. Select only required columns."
NNS/GNS Examples Unified

Consistent format for both NNS and GNS logic
Clear [x] placeholder notation
