import { cn } from "@/lib/utils";
import { InputHTMLAttributes } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
}

export function Input({ label, className, id, ...props }: InputProps) {
  const inputId = id ?? label?.toLowerCase().replace(/\s+/g, "-");
  return (
    <label className="block">
      {label && (
        <span className="mb-2 block text-sm font-semibold text-slate-800">
          {label}
        </span>
      )}
      <input
        id={inputId}
        className={cn(
          "w-full border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900",
          "outline-none transition placeholder:text-slate-400",
          "focus:border-slate-950 focus:ring-2 focus:ring-slate-950/10",
          className
        )}
        {...props}
      />
    </label>
  );
}
