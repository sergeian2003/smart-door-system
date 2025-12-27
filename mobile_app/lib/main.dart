import 'package:flutter/material.dart';
import 'package:flutter_mjpeg/flutter_mjpeg.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:local_auth/local_auth.dart';
import 'package:flutter/foundation.dart';
import 'package:logging/logging.dart';

final _logger = Logger('SmartDoor');

void main() {
  runApp(const SmartDoorApp());
}

class SmartDoorApp extends StatelessWidget {
  const SmartDoorApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Smart Door Secure',
      theme: ThemeData(primarySwatch: Colors.blue, useMaterial3: true),
      home: const DoorControlScreen(),
    );
  }
}

class DoorControlScreen extends StatefulWidget {
  const DoorControlScreen({super.key});

  @override
  State<DoorControlScreen> createState() => _DoorControlScreenState();
}

class _DoorControlScreenState extends State<DoorControlScreen> {
  // --- SETTINGS ---
  static const String baseUrl = 'http://YOUR_RPI_IP:8000';
  // -----------------

  bool isOpening = false;
  List<dynamic> logs = [];
  
  final storage = const FlutterSecureStorage();
  final LocalAuthentication auth = LocalAuthentication();
  String? apiKey;

  @override
  void initState() {
    super.initState();
    _loadApiKey();
    fetchLogs();
  }

  Future<void> _loadApiKey() async {
    String? key = await storage.read(key: 'api_key');
    if (key == null) {
      if (mounted) {
        _showApiKeyDialog();
      }
    } else {
      setState(() => apiKey = key);
    }
  }

  void _showApiKeyDialog() {
    TextEditingController controller = TextEditingController();
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('Security Setup'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(labelText: 'Enter API Key'),
        ),
        actions: [
          TextButton(
            onPressed: () async {
              if (controller.text.isNotEmpty) {
                await storage.write(key: 'api_key', value: controller.text);
                setState(() => apiKey = controller.text);
                if (mounted) {
                  Navigator.pop(context);
                }
              }
            },
            child: const Text('Save'),
          )
        ],
      ),
    );
  }

  Future<void> openDoorSecure() async {
    if (apiKey == null) {
      _showApiKeyDialog();
      return;
    }

    if (kIsWeb) {
      _sendOpenRequest(); 
      return; 
    }

    bool canCheckBiometrics = await auth.canCheckBiometrics;
    bool authenticated = false;

    if (canCheckBiometrics) {
      try {
        authenticated = await auth.authenticate(
          localizedReason: 'Confirm your identity to open the door',
          options: const AuthenticationOptions(biometricOnly: false),
        );
      } catch (e) {
        _logger.warning("Biometric error: $e");
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Biometric error: $e')),
          );
        }
        return;
      }
    } else {
      authenticated = true; 
    }

    if (authenticated) {
      _sendOpenRequest();
    }
  }

  Future<void> _sendOpenRequest() async {
    setState(() => isOpening = true);
    try {
      final response = await http.post(
        Uri.parse('$baseUrl/open_remote'),
        headers: {'x-api-key': apiKey!},
      );

      if (mounted) {
        if (response.statusCode == 200) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('🔓 Access granted! Door is opening.'), backgroundColor: Colors.green),
          );
          fetchLogs();
        } else if (response.statusCode == 403) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('⛔ Invalid API Key!'), backgroundColor: Colors.red),
          );
          _showApiKeyDialog();
        } else {
          throw Exception('Server error: ${response.statusCode}');
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error: $e'), backgroundColor: Colors.red),
        );
      }
    } finally {
      setState(() => isOpening = false);
    }
  }

  Future<void> fetchLogs() async {
    try {
      final response = await http.get(Uri.parse('$baseUrl/logs'));
      if (response.statusCode == 200) {
        setState(() {
          logs = json.decode(utf8.decode(response.bodyBytes));
        });
      }
    } catch (e) {
      _logger.warning("Logs error: $e");
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Smart Door'),
        actions: [
          IconButton(
            icon: const Icon(Icons.vpn_key),
            onPressed: _showApiKeyDialog,
          )
        ],
      ),
      body: Column(
        children: [
          Container(
            height: 250,
            width: double.infinity,
            color: Colors.black,
            child: kIsWeb 
                ? Image.network(
                    '$baseUrl/video_feed',
                    fit: BoxFit.cover,
                    gaplessPlayback: true,
                    errorBuilder: (ctx, err, stack) => const Center(child: Text('Video error', style: TextStyle(color: Colors.white))),
                  )
                : Mjpeg(
                    isLive: true,
                    stream: '$baseUrl/video_feed',
                    error: (context, error, stack) => const Center(
                        child: Text('No camera connection', style: TextStyle(color: Colors.white))),
                  ),
          ),
          const SizedBox(height: 30),
          
          SizedBox(
            width: 200,
            height: 60,
            child: ElevatedButton.icon(
              onPressed: isOpening ? null : openDoorSecure,
              icon: const Icon(Icons.fingerprint, size: 30),
              label: Text(isOpening ? "..." : "OPEN"),
              style: ElevatedButton.styleFrom(
                backgroundColor: isOpening ? Colors.grey : Colors.blue,
                foregroundColor: Colors.white,
              ),
            ),
          ),

          const SizedBox(height: 20),
          const Divider(),
          Expanded(
            child: ListView.builder(
              itemCount: logs.length,
              itemBuilder: (context, index) {
                final log = logs[index];
                return ListTile(
                  leading: Icon(
                    log['method'] == 'face' ? Icons.face : Icons.security,
                    color: Colors.grey,
                  ),
                  title: Text(log['name']),
                  subtitle: Text(log['timestamp'].toString().split('.')[0]),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}