import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SubjectBadge } from '@/components/subjects/SubjectBadge';

describe('SubjectBadge', () => {
  it('renders a repeat badge that opens the subject', () => {
    const onOpen = vi.fn();
    render(
      <SubjectBadge
        fields={{ faceStatus: 'matched', subjectId: 's1', subjectCode: 'S-003', subjectIncidentCount: 3 }}
        onOpen={onOpen}
      />,
    );
    const badge = screen.getByTestId('subject-badge');
    expect(badge).toHaveTextContent('S-003 · Repeat ×3');
    fireEvent.click(badge);
    expect(onOpen).toHaveBeenCalledWith('s1');
  });

  it('renders nothing while the face result is pending', () => {
    const { container } = render(<SubjectBadge fields={{ faceStatus: null }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('is not clickable for "Face not captured"', () => {
    const onOpen = vi.fn();
    render(<SubjectBadge fields={{ faceStatus: 'no_face' }} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId('subject-badge'));
    expect(onOpen).not.toHaveBeenCalled();
  });
});
