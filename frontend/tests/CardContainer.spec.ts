// @vitest-environment jsdom
import { mount } from '@vue/test-utils';
import { describe, it, expect, beforeEach } from 'vitest';
import CardContainer from '../src/components/CardContainer.vue';
import { setActivePinia, createPinia } from 'pinia';
import { useCapabilitiesStore } from '../src/stores/capabilities';

describe('CardContainer Offline UI Masking (Rule 6.2.1)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('applies backdrop filter and pointer-events-none when hardware is offline', async () => {
    const store = useCapabilitiesStore();
    
    // Simulate probe dropping capability state
    store.caps = {
      'media_engine': { status: 'offline', endpoint: '/mnt/media' }
    };
    
    const wrapper = mount(CardContainer, {
      global: {
        stubs: {
          SwitchItem: true,
          SkeletonCard: true
        }
      }
    });
    
    const html = wrapper.html();
    
    // Rule 6.2.1: Blur Isolation Overlay applies
    expect(html).toContain('backdrop-blur');
    expect(html).toContain('pointer-events-none');
    
    // Text rendered
    expect(wrapper.text()).toContain('设备维护中');
  });
});
