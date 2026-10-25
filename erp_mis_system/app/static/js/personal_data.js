/**
 * Personal Data Page - Lazy Loading & Performance Optimizations
 * Uses IntersectionObserver to load sections only when they enter viewport
 */

(() => {
  'use strict';

  // Configuration
  const SECTIONS = [
    'personal',
    'contact',
    'employment',
    'bank',
    'audit'
  ];

  const LOADED_SECTIONS = new Set();
  const OBSERVER_OPTIONS = {
    root: null,
    rootMargin: '100px', // Start loading 100px before section enters viewport
    threshold: 0.01
  };

  // Create IntersectionObserver for lazy loading
  const sectionObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const wrapper = entry.target;
        const section = wrapper.dataset.section;
        
        if (!LOADED_SECTIONS.has(section)) {
          loadSection(wrapper, section);
          LOADED_SECTIONS.add(section);
        }
        
        // Stop observing this section
        sectionObserver.unobserve(wrapper);
      }
    });
  }, OBSERVER_OPTIONS);

  // Load section content via fetch
  async function loadSection(wrapper, section) {
    const url = wrapper.dataset.sectionUrl;
    
    // Add loading state
    wrapper.classList.add('loading');
    
    try {
      const response = await fetch(url, {
        headers: {
          'HX-Request': 'true',
          'Accept': 'text/html'
        },
        credentials: 'same-origin'
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
      const html = await response.text();
      
      // Replace skeleton with actual content
      wrapper.innerHTML = html;
      wrapper.classList.remove('loading');
      wrapper.classList.add('loaded');
      
      // Trigger fade-in animation
      requestAnimationFrame(() => {
        wrapper.querySelectorAll('.personal-data-card').forEach(card => {
          card.classList.add('section-content');
        });
      });
      
      console.log(`Loaded section: ${section}`);
      
    } catch (error) {
      console.error(`Failed to load section ${section}:`, error);
      wrapper.classList.remove('loading');
      wrapper.innerHTML = `
        <div class="personal-data-card text-center py-4">
          <div class="text-danger">
            <i class="bi bi-exclamation-triangle fs-1"></i>
            <p class="mt-2 mb-0">Failed to load ${section} section</p>
            <button class="btn btn-sm btn-outline-primary mt-2" onclick="retrySection('${section}')">
              <i class="bi bi-arrow-clockwise me-1"></i>Retry
            </button>
          </div>
        </div>
      `;
    }
  }

  // Retry loading a section
  window.retrySection = function(section) {
    const wrapper = document.querySelector(`[data-section="${section}"]`);
    if (wrapper) {
      LOADED_SECTIONS.delete(section);
      sectionObserver.observe(wrapper);
    }
  };

  // Initialize lazy loading on DOM ready
  function initLazyLoading() {
    // Observe all section wrappers
    document.querySelectorAll('.section-wrapper').forEach(wrapper => {
      const section = wrapper.dataset.section;
      
      // Don't lazy load if already in viewport (for faster initial render)
      const rect = wrapper.getBoundingClientRect();
      const isInViewport = (
        rect.top <= (window.innerHeight || document.documentElement.clientHeight) &&
        rect.bottom >= 0
      );
      
      if (isInViewport && rect.top < 200) {
        // Load immediately if near top of viewport
        loadSection(wrapper, section);
        LOADED_SECTIONS.add(section);
      } else {
        // Otherwise observe for lazy loading
        sectionObserver.observe(wrapper);
      }
    });
  }

  // Preload critical resources
  function preloadResources() {
    // Preload the CSS if not already loaded
    const cssLink = document.querySelector('link[href*="personal_data.css"]');
    if (cssLink) {
      cssLink.rel = 'preload';
      cssLink.as = 'style';
      cssLink.onload = () => { cssLink.rel = 'stylesheet'; };
    }
  }

  // Navigation optimization: prefetch on hover
  function initNavigationPrefetch() {
    const navLinks = document.querySelectorAll('.verification-breadcrumb a, .verification-heading a');
    
    navLinks.forEach(link => {
      let prefetched = false;
      
      link.addEventListener('mouseenter', () => {
        if (!prefetched && link.href && link.href.startsWith(window.location.origin)) {
          const prefetchLink = document.createElement('link');
          prefetchLink.rel = 'prefetch';
          prefetchLink.href = link.href;
          document.head.appendChild(prefetchLink);
          prefetched = true;
        }
      }, { once: true });
    });
  }

  // Performance monitoring
  function initPerformanceMonitoring() {
    if ('performance' in window && 'PerformanceObserver' in window) {
      try {
        const observer = new PerformanceObserver((list) => {
          const entries = list.getEntries();
          entries.forEach(entry => {
            if (entry.entryType === 'navigation') {
              console.log(`Page load: ${entry.loadEventEnd - entry.loadEventStart}ms`);
            }
            if (entry.entryType === 'resource' && entry.name.includes('personal_data')) {
              console.log(`Resource ${entry.name}: ${entry.duration}ms`);
            }
          });
        });
        observer.observe({ entryTypes: ['navigation', 'resource'] });
      } catch (e) {
        // PerformanceObserver not fully supported
      }
    }
  }

  // Initialize everything when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      initLazyLoading();
      initNavigationPrefetch();
      initPerformanceMonitoring();
    });
  } else {
    initLazyLoading();
    initNavigationPrefetch();
    initPerformanceMonitoring();
  }

  // Handle back/forward cache (bfcache) restoration
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) {
      // Page restored from bfcache, re-initialize lazy loading
      LOADED_SECTIONS.clear();
      initLazyLoading();
    }
  });

})();