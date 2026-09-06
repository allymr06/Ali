/* ════════════════════════════════════════════════════════════════════
   JARVIS · NOVA — main
   Wires the modules together and boots. The bridge is resolved first;
   when it cannot be, the page shows why and nothing is simulated.
   ════════════════════════════════════════════════════════════════════ */
"use strict";

async function main() {
  if (store("nova.theme") === "light") document.body.classList.add("light");
  document.body.classList.toggle("reduced-motion", State.reducedMotion);

  buildRail();
  bindShell();
  bindConversation();
  bindActivity();
  bindPanels();
  bindMedical();
  Engine.staticFrame = State.reducedMotion;
  Engine.init();

  $$("#rail .nav-btn")[0].classList.add("active");
  $(".screen[data-screen='home']").classList.add("active");
  requestAnimationFrame(() => { moveRailIndicator(); Engine.resize(); });

  let bootData = null;
  if (demoRequested()) {
    Bridge = DemoBridge;
    bootData = await Bridge.boot();
  } else {
    const connected = await resolveBridge();
    if (!connected) {
      showBootFailure(
        "Python çekirdeğiyle bağlantı kurulamadı (pywebview köprüsü " +
        "zamanında gelmedi). Hiçbir veri gösterilmiyor ve hiçbir eylem " +
        "simüle edilmiyor. Uygulamayı yeniden başlatmayı dene; sorun " +
        "sürerse JARVIS'i --classic bayrağıyla açabilirsin.");
      return;
    }
    try {
      bootData = await Bridge.boot();
    } catch (err) {
      showBootFailure("Çekirdek başlatılamadı: " + describeError(err));
      return;
    }
    if (!bootData || !bootData.snapshot) {
      showBootFailure("Çekirdek geçerli bir başlangıç durumu döndürmedi.");
      return;
    }
  }
  applyBoot(bootData);
  refreshQuickActions();
  requestAnimationFrame(() => { moveRailIndicator(); Engine.resize(); });
  await runBootSequence(bootData);
  if (State.demo) toast("Demo modu: çekirdek bağlı değil, tüm veriler örnektir.", true);
}

document.addEventListener("DOMContentLoaded", main);
