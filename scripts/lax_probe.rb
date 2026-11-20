# What does Jekyll's lax Liquid actually compute for the root-template lines that fail strict parsing?
require 'liquid'
data = {
  'site' => { 'data' => {
    'pages' => { 'index.html' => { 'label' => '1.0' }, 'a.html' => { 'label' => '2.3' } },
    'resources' => { 'StructureDefinition/x' => { 'history' => true } },
    'structuredefinitions' => { 'x' => { 'type' => 'Patient' } },
    'languages' => { 'langs' => [{ 'code' => 'en' }, { 'code' => 'fr' }] } } },
  'localPage' => 'a.html', 'resource_' => 'StructureDefinition/x', 'include' => { 'id' => 'x' }
}
probes = {
  'pagebegin label'      => ["{% assign label = {{site.data.pages[localPage].label}} | split: '.0' %}[{{label | join: ','}}]",
                             "{% assign label = site.data.pages[localPage].label | split: '.0' %}[{{label | join: ','}}]"],
  'navtabs history'      => ["{% if site.data.resources[resource_].['history'] %}YES{% else %}NO{% endif %}",
                             "{% if site.data.resources[resource_].history %}YES{% else %}NO{% endif %}"],
  'profile-navtabs type' => ["{% assign t = site.data.structuredefinitions.[include.id].type %}[{{t}}]",
                             "{% assign t = site.data.structuredefinitions[include.id].type %}[{{t}}]"],
  'language size()'      => ["{% if site.data.languages.langs.size() != 1 %}MULTI{% else %}SINGLE{% endif %}",
                             "{% if site.data.languages.langs.size != 1 %}MULTI{% else %}SINGLE{% endif %}"],
}
probes.each do |name, (as_written, intended)|
  lax = Liquid::Template.parse(as_written, error_mode: :lax).render(data)
  fixed = Liquid::Template.parse(intended, error_mode: :strict).render(data)
  puts format('%-22s lax(as written)=%-10s strict(fixed)=%-10s %s', name, lax, fixed, lax == fixed ? 'same' : 'DIFFERENT')
end
