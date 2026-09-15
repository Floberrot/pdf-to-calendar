// Retour visuel pendant les traitements serveur (extraction, ecriture agenda...) :
// pas de JS framework, juste un etat "en cours" sur le bouton du formulaire soumis.
// Purement visuel : si ce script ne se charge pas, les formulaires marchent quand meme.
document.addEventListener("submit", (event) => {
  const button = event.target.querySelector("button[type=submit]");
  if (button) {
    button.disabled = true;
    button.classList.add("is-loading");
  }
});
