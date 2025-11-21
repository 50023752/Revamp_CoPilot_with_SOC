"""
Summarize bulk test CSV results.
Usage:
  python tests/summarize_bulk_results.py --csv tests/results/bulk_test_YYYYMMDD_HHMMSS.csv

If no CSV provided, finds the latest file in `tests/results/`.
Produces:
 - summary JSON and CSV with per-question and overall metrics
"""
import argparse
from pathlib import Path
import pandas as pd
import json
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
RESULTS_DIR = PROJECT_ROOT / 'tests' / 'results'


def summarize(csv_path: Path):
    df = pd.read_csv(csv_path)
    summary = {}

    summary['total_tests'] = len(df)
    summary['successful_tests'] = int(df['success'].sum())
    summary['success_rate_pct'] = float(df['success'].mean() * 100)
    summary['avg_duration_ms'] = float(df['duration_ms'].mean())

    # Per question
    per_q = df.groupby('question_id').agg(
        question_count=('success', 'count'),
        success_count=('success', 'sum'),
        success_rate_pct=('success', 'mean'),
        avg_duration_ms=('duration_ms', 'mean')
    ).reset_index()
    per_q['success_rate_pct'] = (per_q['success_rate_pct'] * 100).round(2)
    per_q['avg_duration_ms'] = per_q['avg_duration_ms'].round(0)

    # Per domain accuracy
    per_domain = df.groupby('expected_domain').agg(
        total=('success', 'count'),
        success=('success', 'sum'),
        success_rate_pct=('success', 'mean')
    ).reset_index()
    per_domain['success_rate_pct'] = (per_domain['success_rate_pct'] * 100).round(2)

    # Quick mismatches: rows where domain_correct is False
    mismatches = df[~df['domain_correct']]

    summary['per_question'] = per_q.to_dict(orient='records')
    summary['per_domain'] = per_domain.to_dict(orient='records')
    summary['mismatch_examples'] = mismatches[['question_id', 'question', 'expected_domain', 'actual_domain']].head(20).to_dict(orient='records')

    # Save
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_json = RESULTS_DIR / f'summary_{ts}.json'
    out_csv = RESULTS_DIR / f'summary_{ts}.csv'

    with open(out_json, 'w') as f:
        json.dump(summary, f, indent=2)

    per_q.to_csv(out_csv, index=False)

    print('Summary written:', out_json, out_csv)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', type=str, help='Path to bulk CSV. If omitted latest in tests/results used.')
    args = parser.parse_args()

    if args.csv:
        csv_path = Path(args.csv)
    else:
        files = sorted(RESULTS_DIR.glob('bulk_test_*.csv'))
        if not files:
            raise SystemExit('No bulk test CSV files found in tests/results/')
        csv_path = files[-1]

    summarize(csv_path)
