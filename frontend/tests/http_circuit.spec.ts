// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import MockAdapter from 'axios-mock-adapter';
import { http, isCircuitOpen } from '../src/utils/http';
import { setActivePinia, createPinia } from 'pinia';

// Mock getRequestId to avoid external dependency issues
vi.mock('../src/utils/requestId', () => ({
  getRequestId: () => 'test-req-id'
}));

describe('HTTP Circuit Breaker (Rule 6.2.3)', () => {
  let mock: MockAdapter;

  beforeEach(() => {
    setActivePinia(createPinia());
    mock = new MockAdapter(http);
    vi.useFakeTimers();
  });

  afterEach(() => {
    mock.restore();
    vi.useRealTimers();
  });

  it('opens circuit on 503 response and intercepts new requests without reaching backend', async () => {
    // Setup initial 503 from backend
    mock.onGet('/v1/capabilities').reply(503, { error: 'Hardware Offline' });
    
    // 1. First request triggers the 503
    try {
      await http.get('/v1/capabilities');
    } catch (e) {
      // Expected
    }
    
    // Circuit is now open
    expect(isCircuitOpen()).toBe(true);
    
    // 2. Second request should be caught by interceptor BEFORE reaching backend
    mock.resetHistory();
    try {
      await http.get('/v1/capabilities');
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : String(e);
      expect(message).toContain('Circuit Breaker OPEN');
    }
    
    // Ensure the request never reached adapter level
    expect(mock.history.get.length).toBe(0); 

    // 3. Fast-forward timer to verify circuit closure
    vi.advanceTimersByTime(16000); // Wait out 15s limit
    expect(isCircuitOpen()).toBe(false);
  });
});
