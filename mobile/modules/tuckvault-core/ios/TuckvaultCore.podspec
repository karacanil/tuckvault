Pod::Spec.new do |s|
  s.name           = 'TuckvaultCore'
  s.version        = '1.0.0'
  s.summary        = 'Tuckvault encryption and file access'
  s.description    = 'The Tuckvault storage format (AES-256-GCM, SHA-256 named blobs) and file reading for the mobile app.'
  s.author         = 'Tuckvault'
  s.homepage       = 'https://github.com/karacanil/tuckvault'
  s.license        = 'MIT'
  s.platforms      = {
    :ios => '15.1'
  }
  s.source         = { git: '' }
  s.static_framework = true

  s.dependency 'ExpoModulesCore'
  s.libraries = 'z'

  # Swift/Objective-C compatibility
  s.pod_target_xcconfig = {
    'DEFINES_MODULE' => 'YES',
  }

  s.source_files = "**/*.{h,m,mm,swift,hpp,cpp}"
end
