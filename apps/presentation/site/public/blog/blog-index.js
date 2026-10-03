// Both Blog editions sort by the publication date already displayed on each card.
// Month-only dates retain their precision; missing dates stay after dated posts.
export function comparePublicationDates(left, right) {
  return left === right ? 0 : left < right ? 1 : -1;
}

function displayedPublicationDate(post) {
  return post.querySelector(".listing-date time[datetime]")?.getAttribute("datetime") ?? "";
}

// Return the cards in publication order without touching the DOM.
export function orderBlogPosts(posts, dateOf = displayedPublicationDate) {
  return [...posts].sort((left, right) =>
    comparePublicationDates(dateOf(left), dateOf(right)),
  );
}

// The shipped markup already lists posts newest first, so the ordinary load must
// not move a node: detaching a card blurs whatever the reader is on, and a slow
// module response can arrive after keyboard navigation has started. Only a real
// reorder rewrites the DOM, and that reorder keeps the focused element.
export function sortBlogIndex(index, { activeElement = null } = {}) {
  const posts = [...index.querySelectorAll(":scope > .post-listing")];
  const ordered = orderBlogPosts(posts);
  if (ordered.every((post, position) => post === posts[position])) return false;
  const focused =
    activeElement &&
    typeof activeElement.focus === "function" &&
    posts.some((post) => post.contains(activeElement))
      ? activeElement
      : null;
  // Move existing nodes, preserving their content, links and event listeners.
  const end = index.querySelector(":scope > .index-note");
  for (const post of ordered) index.insertBefore(post, end);
  if (focused) focused.focus({ preventScroll: true });
  return true;
}

if (typeof document !== "undefined") {
  const index = document.querySelector(".blog-index");
  if (index) sortBlogIndex(index, { activeElement: document.activeElement });
}
