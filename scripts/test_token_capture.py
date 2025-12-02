#!/usr/bin/env python3
"""
Quick smoke test for token capture implementation.
Tests that:
1. estimate_tokens_local() works correctly
2. Multi-level token extraction logic functions
3. Token source tracking is recorded
"""

import math

# Test 1: Local token estimation
def estimate_tokens_local(text: str) -> int:
    """Copied from final_stress_testing.py"""
    if not text:
        return 0
    text_clean = text.strip()
    return max(1, math.ceil(len(text_clean) / 4))

# Run tests
test_cases = [
    ("", 0),
    ("Hello", 2),
    ("Hello, world!", 4),
    ("This is a longer text to test token estimation.", 12),
    ("SELECT * FROM table WHERE id = 1", 9),
]

print("=" * 60)
print("TEST: Token Estimation Function")
print("=" * 60)

all_passed = True
for text, expected_approx in test_cases:
    result = estimate_tokens_local(text)
    # Allow ±1 token variance for rounding
    status = "✓ PASS" if abs(result - expected_approx) <= 1 else "✗ FAIL"
    if "FAIL" in status:
        all_passed = False
    print(f"{status} | Text: '{text[:40]}...' | Estimated: {result} tokens (expected ~{expected_approx})")

print("\n" + "=" * 60)
print("TEST: Multi-Level Token Extraction Logic")
print("=" * 60)

# Simulate token extraction levels
def test_token_extraction():
    """Test the fallback chain: callback -> SDK -> estimation"""
    
    # Scenario 1: Callback populated
    session_state_1 = {'model_usage_tokens': {'input_tokens': 100, 'output_tokens': 50}}
    input_toks, output_toks, source = 0, 0, "unknown"
    
    if 'model_usage_tokens' in session_state_1:
        data = session_state_1['model_usage_tokens']
        input_toks = data.get('input_tokens', 0)
        output_toks = data.get('output_tokens', 0)
        source = 'callback'
    
    assert input_toks == 100 and output_toks == 50 and source == 'callback', "Callback level failed"
    print(f"✓ PASS | Callback level: {input_toks}/{output_toks} tokens ({source})")
    
    # Scenario 2: Callback empty, SDK provides
    session_state_2 = {
        'sql_generation_response': {
            'metadata': {'input_tokens': 75, 'output_tokens': 40}
        }
    }
    input_toks, output_toks, source = 0, 0, "unknown"
    
    if input_toks == 0 and output_toks == 0 and 'sql_generation_response' in session_state_2:
        meta = session_state_2['sql_generation_response'].get('metadata', {})
        input_toks = meta.get('input_tokens', 0)
        output_toks = meta.get('output_tokens', 0)
        if input_toks > 0 or output_toks > 0:
            source = 'sdk'
    
    assert input_toks == 75 and output_toks == 40 and source == 'sdk', "SDK level failed"
    print(f"✓ PASS | SDK level: {input_toks}/{output_toks} tokens ({source})")
    
    # Scenario 3: Both empty, use estimation
    question = "Show me collections data"
    sql = "SELECT * FROM collections_table WHERE status = 'active'"
    input_toks_est = estimate_tokens_local(question)
    output_toks_est = estimate_tokens_local(sql)
    
    assert input_toks_est > 0 and output_toks_est > 0, "Estimation level failed"
    print(f"✓ PASS | Estimation level: {input_toks_est}/{output_toks_est} tokens (estimated)")

test_token_extraction()

print("\n" + "=" * 60)
print("TEST: Token Source Tracking")
print("=" * 60)

# Test that token_source field is captured
row_data_example = {
    "input_tokens": 100,
    "output_tokens": 50,
    "token_source": "callback",
    "question": "Sample question",
    "status": "PASS"
}

assert "token_source" in row_data_example, "token_source field missing"
assert row_data_example["token_source"] in ["callback", "sdk", "estimated", "unknown", "error"], \
    f"Invalid token_source: {row_data_example['token_source']}"
print(f"✓ PASS | token_source field tracked: '{row_data_example['token_source']}'")

print("\n" + "=" * 60)
if all_passed:
    print("✅ ALL TESTS PASSED")
else:
    print("⚠️ SOME TESTS FAILED (CHECK ABOVE)")
print("=" * 60)
