import { describe, it, expect } from 'vitest';
import { topRole } from './AuthContext';

// CI-51: the sidebar badge shows the highest app role the user holds.
describe('topRole (CI-51)', () => {
  it('picks owner over admin and viewer regardless of order', () => {
    expect(topRole(['viewer', 'owner', 'admin'])).toBe('owner');
  });

  it('picks admin when owner is absent', () => {
    expect(topRole(['viewer', 'admin'])).toBe('admin');
  });

  it('returns viewer for a viewer-only user', () => {
    expect(topRole(['viewer'])).toBe('viewer');
  });

  it('ignores Keycloak default roles that are not app roles', () => {
    expect(topRole(['offline_access', 'uma_authorization', 'admin'])).toBe('admin');
  });

  it('returns null when no app role is held', () => {
    expect(topRole([])).toBeNull();
    expect(topRole(['offline_access'])).toBeNull();
  });

  it('does not match prototype keys as roles', () => {
    expect(topRole(['toString', 'constructor'])).toBeNull();
  });
});
