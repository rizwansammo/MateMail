import { redirect } from "next/navigation";

export default function LogsPage() {
  // Activity Logs are temporarily hidden from the customer Workspace until
  // the production route is ready. Keep direct/bookmarked visits graceful
  // instead of exposing a broken application page.
  redirect("/app");
}
