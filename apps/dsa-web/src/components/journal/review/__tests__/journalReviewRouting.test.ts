import { describe, expect, it } from 'vitest';
import { journalReturnPath, positionReviewPath } from '../journalReviewRouting';

describe('journal review routing context', () => {
  it('keeps the immutable build and list filters on the review deep link', () => {
    const source = new URLSearchParams(
      'tab=positions&build_id=9&symbol=NVDA&status=open&completeness=partial&case=largest_fee&page=3&style=legacy',
    );

    expect(positionReviewPath(41, source)).toBe(
      '/journal/review/41?build_id=9&symbol=NVDA&status=open&completeness=partial&case=largest_fee&page=3',
    );
  });

  it('returns to the same journal list context without leaking unrelated legacy filters', () => {
    const source = new URLSearchParams(
      'build_id=9&symbol=NVDA&status=open&completeness=partial&case=largest_fee&page=3&style=legacy',
    );

    expect(journalReturnPath(source)).toBe(
      '/journal?tab=positions&build_id=9&symbol=NVDA&status=open&completeness=partial&case=largest_fee&page=3',
    );
  });
});
