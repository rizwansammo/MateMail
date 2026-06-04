import { cn } from "@/lib/utils";

export function StatusBadge({ value }: { value: string }) {
  const v = value.toLowerCase();
  const cls =
    v.includes("active") || v.includes("pass") || v.includes("verified") ||
    v.includes("success") || v.includes("completed") || v.includes("healthy")
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : v.includes("warning") || v.includes("pending") || v.includes("storage")
      ? "border-amber-200 bg-amber-50 text-amber-700"
      : v.includes("missing") || v.includes("fail") || v.includes("blocked") ||
        v.includes("disabled") || v.includes("error")
      ? "border-rose-200 bg-rose-50 text-rose-700"
      : "border-slate-200 bg-slate-50 text-slate-600";

  return (
    <span
      className={cn(
        "inline-flex items-center border px-2 py-1 text-xs font-medium",
        cls
      )}
    >
      {value}
    </span>
  );
}
