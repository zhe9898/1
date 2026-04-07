// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import MockAdapter from 'axios-mock-adapter';
import { http, isCircuitOpen, resetCircuit } from '../src/utils/http';
import { setActivePinia, createPinia } from 'pinia';
import { useAuthStore } from '../src/stores/auth';

// Mock getRequestId to avoid external dependency issues
vi.mock('../src/utils/requestId', () => ({
  getRequestId: () => 'test-req-id'
}));

describe('HTTP Circuit Breaker (Rule 6.2.3)', () => {
  let mock: MockAdapter;

  beforeEach(() => {
    setActivePinia(createPinia());
    resetCircuit();
    mock = new MockAdapter(http);
    vi.useFakeTimers();
  });

  afterEach(() => {
    mock.restore();
    resetCircuit();
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

describe('HTTP Request Headers (CSRF & Identity)', () => {
  let mock: MockAdapter;

  beforeEach(() => {
    setActivePinia(createPinia());
    resetCircuit();
    mock = new MockAdapter(http);
    vi.useFakeTimers();
  });

  afterEach(() => {
    mock.restore();
    resetCircuit();
    vi.useRealTimers();
  });

  it('sends X-Requested-With: XMLHttpRequest on every request (CSRF mitigation)', async () => {
    mock.onGet('/v1/nodes').reply(200, { data: [] });

    await http.get('/v1/nodes');

    const request = mock.history.get[0];
    expect(request).toBeDefined();
    expect(request.headers?.['X-Requested-With']).toBe('XMLHttpRequest');
  });

  it('sends X-Request-ID header on every request (traceability)', async () => {
    mock.onGet('/v1/nodes').reply(200, { data: [] });

    await http.get('/v1/nodes');

    const request = mock.history.get[0];
    expect(request).toBeDefined();
    expect(request.headers?.['X-Request-ID']).toBe('test-req-id');
  });

  it('does not attach Authorization when auth state is cookie-primary', async () => {
    const store = useAuthStore();
    store.setSessionClaims({
      sub: 'u1',
      username: 'alice',
      role: 'admin',
      tenant_id: 'tenant-a',
      scopes: [],
      ai_route_preference: 'auto',
      exp: Math.floor(Date.now() / 1000) + 60,
    });
    mock.onGet('/v1/nodes').reply(200, { data: [] });

    await http.get('/v1/nodes');

    const request = mock.history.get[0];
    expect(request).toBeDefined();
    expect(request.headers?.Authorization).toBeUndefined();
  });
});
