# Strict-mode Liquid parse of IG/template sources, to find content that only works under
# Jekyll's default lax error mode, plus filters that don't exist (silently ignored by Jekyll).
#
# usage (inside the publisher image): ruby strict_check.rb <root> <out.jsonl>
#   <root> contains repos/ and templates/ (mounted read-only)
require 'jekyll'
require 'json'
require 'find'

ROOT, OUT = ARGV

# Publisher-expanded tags never reach Jekyll (PublisherGenerator keyword list); strip them like it does.
PUBLISHER_TAGS = /\{%\s*(sql|fragment|json|class-diagram|uml|multi-map|lang-fragment|dataset|sqlToData)\b.*?%\}/m

KNOWN_FILTERS = (Liquid::StandardFilters.public_instance_methods +
                 Jekyll::Filters.public_instance_methods).map(&:to_s).to_set

PAGE_DIRS = %w[input/pagecontent input/intro-notes input/includes input/pages input/pagecontents
               fsh-generated/includes].freeze
EXTS = %w[.md .html .xml .xhtml .htm].freeze

def each_filter(node, &blk)
  case node
  when Liquid::Variable
    node.filters.each { |f| blk.call(f[0]) }
  when Liquid::Assign
    each_filter(node.from, &blk)
  end
  node.nodelist.each { |n| each_filter(n, &blk) } if node.respond_to?(:nodelist) && node.nodelist
  if node.is_a?(Liquid::If) || node.is_a?(Liquid::Case)
    node.blocks.each { |b| each_filter(b.attachment, &blk) if b.attachment }
  end
end

def prep(src, template)
  src = src.gsub(/\{%!.*?%\}/m, '')                  # publisher escape syntax
  src = src.gsub(PUBLISHER_TAGS, '')
  src = src.gsub(/\{\{\[[^\]]*\]\}\}/, 'X') if template # publisher pre-Jekyll placeholders
  src
end

def files_for(unit_dir, template)
  return [] unless Dir.exist?(unit_dir)
  out = []
  Find.find(unit_dir) do |p|
    if File.directory?(p)
      # liquid/ holds FHIRPath narrative templates run by the publisher's own engine, not Jekyll
      Find.prune if %w[.git node_modules temp output .jekyll-cache liquid].include?(File.basename(p))
      next
    end
    next unless EXTS.include?(File.extname(p).downcase)
    rel = p.delete_prefix(unit_dir + '/')
    next unless template || PAGE_DIRS.any? { |d| rel.include?(d + '/') }
    next if !template && rel =~ %r{(^|/)(template|ig-template|local-template|custom-template)[^/]*/}
    out << p
  end
  out
end

units = []
Dir.children(File.join(ROOT, 'repos')).sort.each { |n| units << ['ig', n, File.join(ROOT, 'repos', n)] }
Dir.children(File.join(ROOT, 'templates')).sort.each { |n| units << ['template', n, File.join(ROOT, 'templates', n)] }

File.open(OUT, 'w') do |out|
  units.each do |kind, name, dir|
    files_for(dir, kind == 'template').each do |p|
      src = File.read(p, mode: 'rb').force_encoding('UTF-8').scrub
      next unless src.include?('{%') || src.include?('{{')
      rel = p.delete_prefix(dir + '/')
      text = prep(src, kind == 'template')
      emit = ->(type, detail) { out.puts({ kind: kind, unit: name, file: rel, type: type, detail: detail }.to_json) }
      t = nil
      begin
        t = Liquid::Template.parse(text, error_mode: :strict, line_numbers: true)
      rescue Liquid::Error => e
        emit.call('strict-syntax', e.message[0, 300])
        begin
          t = Liquid::Template.parse(text, error_mode: :lax, line_numbers: true) # what Jekyll does
        rescue Liquid::Error => e2
          emit.call('lax-syntax', e2.message[0, 300]) # would fail under real Jekyll too
        end
      rescue StandardError => e
        emit.call('other', "#{e.class}: #{e.message[0, 200]}")
      end
      if t
        bad = []
        each_filter(t.root) { |f| bad << f unless KNOWN_FILTERS.include?(f) }
        bad.uniq.each { |f| emit.call('unknown-filter', f) }
      end
    end
  end
end
