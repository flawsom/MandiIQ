/**
 * Canvas Cursor Effect - Vanilla JS
 * Touch-responsive spring physics cursor trails
 * Works on desktop (mouse) and mobile (touch)
 */
(function() {
  'use strict';

  // Skip on touch-only devices for performance
  const isTouchOnly = ('ontouchstart' in window) && !window.matchMedia('(pointer: fine)').matches;
  if (isTouchOnly) return;

  // Configuration
  const CONFIG = {
    friction: 0.5,
    trails: 20,
    size: 50,
    dampening: 0.25,
    tension: 0.98
  };

  let canvas, ctx, running = true, frame = 1;
  let pos = { x: 0, y: 0 };
  let lines = [];
  let hueOffset = 0;

  // Oscillator for color cycling
  function Oscillator(cfg) {
    this.phase = cfg.phase || 0;
    this.offset = cfg.offset || 0;
    this.frequency = cfg.frequency || 0.001;
    this.amplitude = cfg.amplitude || 1;
  }
  Oscillator.prototype.update = function() {
    this.phase += this.frequency;
    this.value = this.offset + Math.sin(this.phase) * this.amplitude;
    return this.value;
  };

  const colorOsc = new Oscillator({
    phase: Math.random() * 2 * Math.PI,
    amplitude: 85,
    frequency: 0.0015,
    offset: 285
  });

  // Node for spring physics
  function Node() {
    this.x = 0;
    this.y = 0;
    this.vy = 0;
    this.vx = 0;
  }

  // Trail line with spring physics
  function Line(spring) {
    this.spring = spring + 0.1 * Math.random() - 0.02;
    this.friction = CONFIG.friction + 0.01 * Math.random() - 0.002;
    this.nodes = [];
    for (var i = 0; i < CONFIG.size; i++) {
      var n = new Node();
      n.x = pos.x;
      n.y = pos.y;
      this.nodes.push(n);
    }
  }

  Line.prototype.update = function() {
    var spring = this.spring;
    var prev = this.nodes[0];
    prev.vx += (pos.x - prev.x) * spring;
    prev.vy += (pos.y - prev.y) * spring;
    for (var i = 0; i < this.nodes.length; i++) {
      var node = this.nodes[i];
      if (i > 0) {
        var p = this.nodes[i - 1];
        node.vx += (p.x - node.x) * spring;
        node.vy += (p.y - node.y) * spring;
        node.vx += p.vx * CONFIG.dampening;
        node.vy += p.vy * CONFIG.dampening;
      }
      node.vx *= this.friction;
      node.vy *= this.friction;
      node.x += node.vx;
      node.y += node.vy;
      spring *= CONFIG.tension;
    }
  };

  Line.prototype.draw = function() {
    ctx.beginPath();
    var n0 = this.nodes[0];
    ctx.moveTo(n0.x, n0.y);
    for (var i = 1; i < this.nodes.length - 2; i++) {
      var e = this.nodes[i];
      var t = this.nodes[i + 1];
      var mx = 0.5 * (e.x + t.x);
      var my = 0.5 * (e.y + t.y);
      ctx.quadraticCurveTo(e.x, e.y, mx, my);
    }
    var last = this.nodes[this.nodes.length - 2];
    var end = this.nodes[this.nodes.length - 1];
    ctx.quadraticCurveTo(last.x, last.y, end.x, end.y);
    ctx.stroke();
    ctx.closePath();
  };

  function createLines() {
    lines = [];
    for (var i = 0; i < CONFIG.trails; i++) {
      lines.push(new Line(0.4 + (i / CONFIG.trails) * 0.025));
    }
  }

  function render() {
    if (!running) return;
    ctx.globalCompositeOperation = 'source-over';
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.globalCompositeOperation = 'lighter';
    var hue = Math.round(colorOsc.update());
    ctx.strokeStyle = 'hsla(' + hue + ',50%,50%,0.2)';
    ctx.lineWidth = 1;
    for (var i = 0; i < lines.length; i++) {
      lines[i].update();
      lines[i].draw();
    }
    frame++;
    requestAnimationFrame(render);
  }

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }

  function handleMove(x, y) {
    pos.x = x;
    pos.y = y;
  }

  function onMouseMove(e) {
    handleMove(e.clientX, e.clientY);
  }

  function onTouchMove(e) {
    if (e.touches.length > 0) {
      handleMove(e.touches[0].pageX, e.touches[0].pageY);
    }
  }

  function init() {
    // Create canvas
    canvas = document.createElement('canvas');
    canvas.id = 'cursor-canvas';
    canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:9999;';
    document.body.appendChild(canvas);

    ctx = canvas.getContext('2d');
    resize();
    createLines();

    // Event listeners - touch responsive
    document.addEventListener('mousemove', onMouseMove, { passive: true });
    document.addEventListener('touchmove', onTouchMove, { passive: true });
    document.addEventListener('touchstart', onTouchMove, { passive: true });

    window.addEventListener('resize', resize);
    window.addEventListener('orientationchange', resize);

    // Pause when tab not visible
    document.addEventListener('visibilitychange', function() {
      if (document.hidden) {
        running = false;
      } else {
        running = true;
        render();
      }
    });

    // Start animation
    render();
  }

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
