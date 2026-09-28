// Retour visuel pendant les traitements serveur (extraction, ecriture agenda...) :
// pas de JS framework, juste un etat "en cours" sur le bouton du formulaire soumis,
// et, si le formulaire porte data-loading, un message d'attente sous le bouton.
// Purement visuel : si ce script ne se charge pas, les formulaires marchent quand meme.
document.addEventListener("submit", (event) => {
  const form = event.target;
  const button = form.querySelector("button[type=submit]");
  if (button) {
    button.disabled = true;
    button.classList.add("is-loading");
  }
  const message = form.dataset.loading;
  if (message && !form.querySelector(".loading-note")) {
    const note = document.createElement("p");
    note.className = "loading-note";
    note.setAttribute("role", "status");
    note.textContent = message;
    if (button) {
      button.insertAdjacentElement("afterend", note);
    } else {
      form.append(note);
    }
  }
});

// Retour arriere du navigateur (cache "bfcache") : la page reapparait telle
// qu'au moment de l'envoi, bouton desactive et message d'attente compris.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) {
    return;
  }
  document.querySelectorAll("button.is-loading").forEach((button) => {
    button.disabled = false;
    button.classList.remove("is-loading");
  });
  document.querySelectorAll(".loading-note").forEach((note) => note.remove());
});
