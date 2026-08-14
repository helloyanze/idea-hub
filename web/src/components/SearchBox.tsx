interface SearchBoxProps {
  value: string
  onChange: (value: string) => void
  onSearch: (value: string) => void
  placeholder?: string
  label: string
}

function SearchBox({
  value,
  onChange,
  onSearch,
  placeholder,
  label,
}: SearchBoxProps) {
  return (
    <input
      type="search"
      aria-label={label}
      placeholder={placeholder}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          onSearch(value)
        }
      }}
      className="h-9 w-full rounded-md border bg-background px-3 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-ring focus:ring-2 focus:ring-ring/30"
    />
  )
}

export { SearchBox }
